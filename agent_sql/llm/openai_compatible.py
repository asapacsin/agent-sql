"""OpenAI-compatible chat-completions adapter (used for Qwen via Alibaba Cloud Model Studio, Singapore).

Translates the provider-neutral message/tool format (agent_sql.llm.base) to chat completions and back.
Credentials: the session's network proxy injects the API key for the configured host; a placeholder bearer is sent
when no key variable is set. No sampling parameters are sent (same policy for every system).
"""
from __future__ import annotations

import json
import os
import time

import httpx

from agent_sql.llm.base import LLMRequest, LLMResponse, ProviderError, Usage

DEFAULT_BASE_URL = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"


def to_chat_messages(system: str, messages: list[dict], json_hint: str | None = None) -> list[dict]:
    out: list[dict] = [{"role": "system", "content": system + (f"\n\n{json_hint}" if json_hint else "")}]
    for m in messages:
        content = m["content"]
        if m["role"] == "user":
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
                continue
            texts = []
            for b in content:
                if b.get("type") == "tool_result":
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": str(b.get("content", ""))})
                elif b.get("type") == "text":
                    texts.append(b["text"])
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
        else:
            if isinstance(content, str):
                out.append({"role": "assistant", "content": content})
                continue
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                     for b in content if b.get("type") == "tool_use"]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
    return out


def from_chat_message(msg: dict) -> list[dict]:
    blocks: list[dict] = []
    if msg.get("content"):
        blocks.append({"type": "text", "text": msg["content"]})
    for call in msg.get("tool_calls") or []:
        raw = call.get("function", {}).get("arguments") or "{}"
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                raise ValueError
        except ValueError:
            args = {"__invalid_json__": raw[:500]}  # fails strict argument validation -> INVALID_ARGS observation
        blocks.append({"type": "tool_use", "id": call.get("id") or f"call_{len(blocks)}",
                       "name": call.get("function", {}).get("name", ""), "input": args})
    return blocks


class OpenAICompatibleClient:
    def __init__(self, base_url: str | None = None, *, api_key_env: str = "DASHSCOPE_API_KEY", timeout_s: float = 600.0,
                 max_retries: int = 2) -> None:
        self.base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = os.environ.get(api_key_env) or "proxy-injected"
        self.client = httpx.Client(timeout=timeout_s)
        self.max_retries = max_retries

    def complete(self, req: LLMRequest) -> LLMResponse:
        hint = None
        body: dict = {"model": req.model, "max_tokens": req.max_tokens}
        if req.output_schema is not None:
            body["response_format"] = {"type": "json_object"}
            hint = ("Respond with a single JSON object (json) matching this JSON Schema exactly:\n"
                    + json.dumps(req.output_schema))
        body["messages"] = to_chat_messages(req.system, req.messages, hint)
        if req.tools:
            body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                               "parameters": t.input_schema}} for t in req.tools]
            body["tool_choice"] = "auto"
        start = time.monotonic()
        resp = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self.client.post(f"{self.base_url}/chat/completions", json=body,
                                        headers={"Authorization": f"Bearer {self.api_key}"})
            except httpx.HTTPError as e:
                if attempt == self.max_retries:
                    raise ProviderError(f"connection error after retries: {type(e).__name__}") from e
                time.sleep(2 ** attempt)
                continue
            if resp.status_code in (408, 409, 429) or resp.status_code >= 500:
                if attempt == self.max_retries:
                    raise ProviderError(f"API status {resp.status_code} after retries")
                time.sleep(2 ** attempt)
                continue
            break
        assert resp is not None
        if resp.status_code in (401, 403):
            raise ProviderError(f"authentication failed ({resp.status_code})", "auth")
        if resp.status_code >= 400:
            raise ProviderError(f"API status {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        choice = data["choices"][0]
        finish = choice.get("finish_reason") or ""
        if finish == "content_filter":
            raise ProviderError("model refused (content_filter)", "refusal")
        u = data.get("usage") or {}
        cached = int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
        usage = Usage(input_tokens=int(u.get("prompt_tokens") or 0) - cached,
                      output_tokens=int(u.get("completion_tokens") or 0), cache_read_input_tokens=cached)
        stop = {"tool_calls": "tool_use", "stop": "end_turn", "length": "max_tokens"}.get(finish, finish)
        return LLMResponse(from_chat_message(choice["message"]), stop, usage, req.model, data.get("model", req.model),
                           time.monotonic() - start, req.request_hash, data.get("id"))
