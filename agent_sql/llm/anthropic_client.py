"""Anthropic Messages API adapter (official SDK). Thinking adaptive; effort explicit; no sampling params."""
from __future__ import annotations

import time

from agent_sql.llm.base import LLMRequest, LLMResponse, ProviderError, Usage


class AnthropicClient:
    def __init__(self, *, max_retries: int = 2, timeout_s: float = 600.0, interactive_fallbacks: bool = False) -> None:
        import anthropic  # imported lazily so offline tests never need credentials

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=max_retries, timeout=timeout_s)
        self.interactive_fallbacks = interactive_fallbacks  # benchmark mode keeps this False (fairness)

    def complete(self, req: LLMRequest) -> LLMResponse:
        a = self._anthropic
        output_config: dict = {"effort": req.effort}
        if req.output_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": req.output_schema}
        kwargs: dict = {
            "model": req.model,
            "max_tokens": req.max_tokens,
            "system": [{"type": "text", "text": req.system}],
            "messages": req.messages,
            "thinking": {"type": "adaptive"},
            "output_config": output_config,
            "cache_control": {"type": "ephemeral"},
        }
        if req.tools:
            kwargs["tools"] = [{**t.to_dict(), "strict": True} for t in req.tools]
            kwargs["tool_choice"] = {"type": "auto"}
        start = time.monotonic()
        try:
            if self.interactive_fallbacks:
                resp = self.client.beta.messages.create(betas=["server-side-fallback-2026-07-01"],
                                                        fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.create(**kwargs)
        except a.AuthenticationError as e:
            raise ProviderError(f"authentication failed: {e.message}", "auth") from e
        except a.BadRequestError as e:
            raise ProviderError(f"bad request: {e.message}", "error") from e
        except a.RateLimitError as e:
            raise ProviderError("rate limited after SDK retries", "error") from e
        except a.APIStatusError as e:
            raise ProviderError(f"API status {e.status_code}: {e.message}", "error") from e
        except a.APIConnectionError as e:
            raise ProviderError("connection error after SDK retries", "error") from e
        latency = time.monotonic() - start
        if resp.stop_reason == "refusal":
            raise ProviderError("model refused the request", "refusal")
        u = resp.usage
        usage = Usage(int(u.input_tokens or 0), int(u.output_tokens or 0),
                      int(getattr(u, "cache_read_input_tokens", 0) or 0),
                      int(getattr(u, "cache_creation_input_tokens", 0) or 0))
        content = [b.model_dump(exclude_none=True) for b in resp.content]
        return LLMResponse(content, resp.stop_reason or "", usage, req.model, resp.model, latency, req.request_hash,
                           getattr(resp, "id", None))
