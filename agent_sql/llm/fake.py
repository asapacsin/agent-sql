"""Deterministic scripted LLM for tests and offline development (never reported as a model result)."""
from __future__ import annotations

import json
from collections.abc import Callable

from agent_sql.llm.base import LLMRequest, LLMResponse, Usage

Script = Callable[[LLMRequest, int], dict | str]


def _approx_tokens(obj) -> int:  # noqa: ANN001
    return max(1, len(json.dumps(obj, default=str)) // 4)


def text_response(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def tool_call(name: str, args: dict, call_id: str = "toolu_1", preface: str | None = None) -> dict:
    content = [{"type": "text", "text": preface}] if preface else []
    content.append({"type": "tool_use", "id": call_id, "name": name, "input": args})
    return {"content": content, "stop_reason": "tool_use"}


class FakeLLM:
    """Responses come from a list (consumed in order) or a callable(request, call_index)."""

    def __init__(self, script: list[dict | str] | Script, model: str = "fake-model",
                 served_model: str | None = None) -> None:
        self.script = script
        self.model = model
        self.served_model = served_model
        self.calls: list[LLMRequest] = []

    def complete(self, req: LLMRequest) -> LLMResponse:
        index = len(self.calls)
        self.calls.append(req)
        if callable(self.script):
            item = self.script(req, index)
        else:
            if index >= len(self.script):
                raise AssertionError(f"FakeLLM script exhausted at call {index}")
            item = self.script[index]
        if isinstance(item, str):
            item = text_response(item)
        usage = Usage(input_tokens=_approx_tokens(req.canonical()), output_tokens=_approx_tokens(item["content"]))
        return LLMResponse(item["content"], item.get("stop_reason", "end_turn"), usage, req.model,
                           self.served_model or req.model, 0.0, req.request_hash, f"fake-{index}")
