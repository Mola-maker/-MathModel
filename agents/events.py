"""Cross-process structured event bus, backed by a JSONL file.

Pipeline runs in a subprocess; the UI server runs in another. Rather than
building a socket/IPC layer, we append newline-delimited JSON events to a log
file that the UI tails. Each event has a monotonic `seq` and wall-clock `ts`,
so consumers can resume after reconnects.

Keep events small and structured — stdout parsing is for humans, this is for
machines. Producers call `emit(kind, **payload)`; consumers tail `EVENTS_FILE`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

# On Linux a single write() on a file opened with O_APPEND is atomic up to
# PIPE_BUF (4096 B) for regular files. All events are well under that limit,
# so this gives us safe cross-process concurrent appends without a file lock.
_APPEND_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT

BASE_DIR = Path(__file__).resolve().parent.parent
VOL_HOST = Path(os.getenv("VOL_HOST", BASE_DIR / "vol"))
EVENTS_DIR = VOL_HOST / "logs"
EVENTS_FILE = EVENTS_DIR / "pipeline_events.jsonl"

_lock = threading.Lock()
_seq = 0


def _next_seq() -> int:
    global _seq
    with _lock:
        _seq += 1
        return _seq


def emit(kind: str, **payload: Any) -> None:
    """Append a structured event to the events file, then fan out to plugin
    subscribers. Never raises.
    """
    event = {"seq": _next_seq(), "ts": time.time(), "kind": kind, **payload}
    try:
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
        fd = os.open(str(EVENTS_FILE), _APPEND_FLAGS, 0o644)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)
    except Exception as exc:  # noqa: BLE001
        # Events must never break the pipeline. Swallow and print once.
        print(f"  [events] emit 失败 ({kind}): {exc}")

    # Dispatch to plugin handlers subscribed via @on_event(kind)
    try:
        from agents.extensions import get_registry
        get_registry().emit_event(kind, event)
    except Exception:
        pass


def reset() -> None:
    """Truncate the events file — call at pipeline start for a fresh run.

    Only safe to call when the pipeline subprocess is not running.
    """
    try:
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(EVENTS_FILE), os.O_WRONLY | os.O_TRUNC | os.O_CREAT, 0o644)
        os.close(fd)
        global _seq
        with _lock:
            _seq = 0
    except Exception as exc:  # noqa: BLE001
        print(f"  [events] reset 失败: {exc}")


def read_all() -> list[dict]:
    """Read all events. Cheap — the file is small and local."""
    if not EVENTS_FILE.exists():
        return []
    out: list[dict] = []
    with EVENTS_FILE.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out
