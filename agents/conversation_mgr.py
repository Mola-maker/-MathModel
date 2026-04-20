"""Conversation manager — server-side persistence of chat sessions.

Stores sessions in context_store/conversations.json. Each session holds a
persona_id and a list of messages compatible with the OpenAI chat format
(role, content, optionally tool_calls or tool_call_id).

Thread-safety: guarded by a module-level lock. Single FastAPI process only.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from agents.persona_mgr import default_persona_id, get_persona


_STORE_PATH = Path(
    os.getenv("CONVERSATIONS_STORE", "context_store/conversations.json")
)
_LOCK = threading.Lock()
_MAX_MESSAGES_PER_SESSION = 200   # rolling window; trims oldest on overflow
_MAX_SESSIONS = 50                # cap to avoid unbounded growth


def _now() -> int:
    return int(time.time())


def _load_all() -> dict:
    if not _STORE_PATH.exists():
        return {"sessions": {}, "order": []}
    try:
        return json.loads(_STORE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"sessions": {}, "order": []}


def _save_all(data: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STORE_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_sessions() -> list[dict]:
    """Return sessions in display order (most recent first)."""
    with _LOCK:
        data = _load_all()
        sessions = data.get("sessions", {})
        order = data.get("order", [])
        result: list[dict] = []
        for sid in order:
            s = sessions.get(sid)
            if not s:
                continue
            result.append({
                "id": s["id"],
                "title": s.get("title", "新对话"),
                "persona": s.get("persona", default_persona_id()),
                "message_count": len(s.get("messages", [])),
                "created_at": s.get("created_at", 0),
                "updated_at": s.get("updated_at", 0),
            })
        return result


def get_session(session_id: str) -> dict | None:
    with _LOCK:
        data = _load_all()
        return data.get("sessions", {}).get(session_id)


def create_session(persona: str | None = None, title: str = "新对话") -> dict:
    persona_id = persona or default_persona_id()
    # validate persona; fall back silently
    persona_id = get_persona(persona_id).id
    with _LOCK:
        data = _load_all()
        sid = "sess_" + uuid.uuid4().hex[:12]
        now = _now()
        session = {
            "id": sid,
            "title": title,
            "persona": persona_id,
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
        data.setdefault("sessions", {})[sid] = session
        order = data.setdefault("order", [])
        order.insert(0, sid)
        # trim oldest if over cap
        while len(order) > _MAX_SESSIONS:
            drop = order.pop()
            data["sessions"].pop(drop, None)
        _save_all(data)
        return session


def delete_session(session_id: str) -> bool:
    with _LOCK:
        data = _load_all()
        sessions = data.get("sessions", {})
        if session_id not in sessions:
            return False
        sessions.pop(session_id, None)
        data["order"] = [s for s in data.get("order", []) if s != session_id]
        _save_all(data)
        return True


def rename_session(session_id: str, title: str) -> bool:
    with _LOCK:
        data = _load_all()
        s = data.get("sessions", {}).get(session_id)
        if not s:
            return False
        s["title"] = title[:60] or "新对话"
        s["updated_at"] = _now()
        _save_all(data)
        return True


def set_persona(session_id: str, persona_id: str) -> bool:
    persona_id = get_persona(persona_id).id
    with _LOCK:
        data = _load_all()
        s = data.get("sessions", {}).get(session_id)
        if not s:
            return False
        s["persona"] = persona_id
        s["updated_at"] = _now()
        _save_all(data)
        return True


def append_messages(session_id: str, new_messages: list[dict]) -> dict | None:
    """Append messages to a session and promote to top of order."""
    if not new_messages:
        return get_session(session_id)
    with _LOCK:
        data = _load_all()
        s = data.get("sessions", {}).get(session_id)
        if not s:
            return None
        msgs: list[dict] = s.setdefault("messages", [])
        msgs.extend(new_messages)
        # auto-title from first user message if still default
        if s.get("title", "新对话") == "新对话":
            for m in msgs:
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    s["title"] = m["content"][:28] or "新对话"
                    break
        # rolling trim
        if len(msgs) > _MAX_MESSAGES_PER_SESSION:
            s["messages"] = msgs[-_MAX_MESSAGES_PER_SESSION:]
        s["updated_at"] = _now()
        # promote
        order = data.setdefault("order", [])
        if session_id in order:
            order.remove(session_id)
        order.insert(0, session_id)
        _save_all(data)
        return s


def session_messages(session_id: str, limit: int | None = None) -> list[dict]:
    s = get_session(session_id)
    if not s:
        return []
    msgs = s.get("messages", [])
    if limit and limit > 0:
        return msgs[-limit:]
    return list(msgs)


def sanitize_for_display(messages: list[dict]) -> list[dict]:
    """Strip internal tool fields for frontend display; keep role/content + a compact tool summary."""
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        if role == "system":
            continue
        if role == "tool":
            # Represent tool results compactly
            out.append({
                "role": "tool",
                "content": m.get("content", ""),
                "tool_name": m.get("name") or m.get("tool_name") or "tool",
            })
            continue
        entry: dict[str, Any] = {"role": role, "content": m.get("content") or ""}
        tc = m.get("tool_calls")
        if tc:
            entry["tool_calls"] = [
                {"name": c.get("function", {}).get("name"),
                 "arguments": c.get("function", {}).get("arguments", "")}
                for c in tc
            ]
        out.append(entry)
    return out
