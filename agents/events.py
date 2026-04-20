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
        with EVENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
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
    """Truncate the events file — call at pipeline start for a fresh run."""
    try:
        EVENTS_DIR.mkdir(parents=True, exist_ok=True)
        EVENTS_FILE.write_text("", encoding="utf-8")
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
