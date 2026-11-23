"""Trace event schema and JSONL writer (docs/spec/ARCHITECTURE.md section 8). No private chain-of-thought."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from agent_sql.tracing.redact import redact

EVENT_TYPES = {"run_start", "state", "llm_call", "tool_call", "tool_result", "budget", "validation", "final"}


def visible_blocks(content: list[dict]) -> list[dict]:
    """Strip thinking/redacted blocks: traces keep only text and tool_use blocks."""
    return [b for b in content if b.get("type") in ("text", "tool_use")]


class Trace:
    """In-memory trace for one task-run, optionally mirrored to a JSONL file."""

    def __init__(self, run_id: str, task_id: str, system_id: str, path: Path | None = None) -> None:
        self.run_id, self.task_id, self.system_id = run_id, task_id, system_id
        self.events: list[dict] = []
        self.path = Path(path) if path else None
        self._t0 = time.monotonic()
        self._lock = threading.Lock()

    def emit(self, type_: str, **fields: Any) -> dict:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown trace event type {type_}")
        if "content" in fields and isinstance(fields["content"], list):
            fields["content"] = visible_blocks(fields["content"])
        event = redact({"run_id": self.run_id, "task_id": self.task_id, "system_id": self.system_id,
                        "seq": len(self.events), "t": round(time.monotonic() - self._t0, 4), "type": type_,
                        **fields})
        with self._lock:
            self.events.append(event)
            if self.path is not None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        return event

    def of_type(self, type_: str) -> list[dict]:
        return [e for e in self.events if e["type"] == type_]
