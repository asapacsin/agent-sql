"""Build the configured live LLM client (provider chosen in configs/models.yaml)."""
from __future__ import annotations

from agent_sql.config.settings import ModelSettings
from agent_sql.llm.base import LLMClient


def live_client(model: ModelSettings, interactive: bool = False) -> LLMClient:
    if model.provider == "openai_compatible":
        from agent_sql.llm.openai_compatible import OpenAICompatibleClient

        return OpenAICompatibleClient(model.base_url, max_retries=model.sdk_max_retries)
    from agent_sql.llm.anthropic_client import AnthropicClient

    return AnthropicClient(max_retries=model.sdk_max_retries, interactive_fallbacks=interactive)
