"""Provider-neutral LLM protocol (docs/spec/ARCHITECTURE.md section 5).

Messages use a provider-neutral subset of the Messages-API shape:
  {"role": "user"|"assistant", "content": str | [block, ...]}
  blocks: {"type": "text", "text"}, {"type": "tool_use", "id", "name", "input"},
          {"type": "tool_result", "tool_use_id", "content", "is_error"}, and opaque provider blocks (e.g. thinking)
          which are passed back unchanged and never displayed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Protocol

from agent_sql.config.settings import canonical_json, sha256_text


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}


@dataclass
class LLMRequest:
    model: str
    system: str
    messages: list[dict]
    max_tokens: int
    effort: str
    tools: list[ToolSpec] = field(default_factory=list)
    output_schema: dict | None = None
    metadata: dict = field(default_factory=dict)  # not sent to the provider, not hashed

    def canonical(self) -> dict:
        return {"model": self.model, "system": self.system, "messages": self.messages, "max_tokens": self.max_tokens,
                "effort": self.effort, "tools": [t.to_dict() for t in self.tools], "output_schema": self.output_schema}

    @property
    def request_hash(self) -> str:
        return sha256_text(canonical_json(self.canonical()))


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_input(self) -> int:
        return self.input_tokens + self.cache_read_input_tokens + self.cache_creation_input_tokens

    def __add__(self, other: Usage) -> Usage:
        return Usage(self.input_tokens + other.input_tokens, self.output_tokens + other.output_tokens,
                     self.cache_read_input_tokens + other.cache_read_input_tokens,
                     self.cache_creation_input_tokens + other.cache_creation_input_tokens)


@dataclass
class LLMResponse:
    content: list[dict]
    stop_reason: str
    usage: Usage
    requested_model: str
    served_model: str
    latency_s: float
    request_hash: str
    request_id: str | None = None

    def text(self) -> str:
        return "".join(b.get("text", "") for b in self.content if b.get("type") == "text")

    def tool_uses(self) -> list[dict]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def to_dict(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> LLMResponse:
        d = dict(d)
        d["usage"] = Usage(**d["usage"])
        return cls(**d)


class ProviderError(RuntimeError):
    """Provider call failed after SDK retries, or was refused."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind  # error | refusal | auth


class LLMClient(Protocol):
    def complete(self, req: LLMRequest) -> LLMResponse: ...


def json_tool_result(tool_use_id: str, payload: Any, is_error: bool = False) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": canonical_json(payload),
            "is_error": is_error}
