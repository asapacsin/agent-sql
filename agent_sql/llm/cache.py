"""Record/replay wrapper: record forwards and stores responses by request hash; replay fails on a miss."""
from __future__ import annotations

import json
import threading
from pathlib import Path

from agent_sql.llm.base import LLMClient, LLMRequest, LLMResponse


class CacheMiss(KeyError):
    pass


class CachedLLM:
    def __init__(self, path: Path, mode: str, inner: LLMClient | None = None) -> None:
        if mode not in {"record", "replay"}:
            raise ValueError(mode)
        if mode == "record" and inner is None:
            raise ValueError("record mode needs an inner client")
        self.path, self.mode, self.inner = Path(path), mode, inner
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self._entries[rec["request_hash"]] = rec["response"]

    def complete(self, req: LLMRequest) -> LLMResponse:
        key = req.request_hash
        with self._lock:
            hit = self._entries.get(key)
        if hit is not None:
            return LLMResponse.from_dict(hit)
        if self.mode == "replay":
            raise CacheMiss(f"replay cache miss for request {key[:12]} ({req.metadata})")
        assert self.inner is not None
        resp = self.inner.complete(req)
        with self._lock:
            self._entries[key] = resp.to_dict()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"request_hash": key, "response": resp.to_dict()}, sort_keys=True) + "\n")
        return resp
