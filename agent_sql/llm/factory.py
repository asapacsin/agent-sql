"""Build the configured live LLM client (provider chosen in configs/models.yaml)."""
from __future__ import annotations

from agent_sql.config.settings import ModelSettings
from agent_sql.llm.base import LLMClient

NO_MODEL = "UNSET"


def live_client(model: ModelSettings, interactive: bool = False) -> LLMClient:
    """Live calls spend the owner's money: they need a model the owner set and an explicit opt-in (AGENTS.md D14)."""
    import os

    if model.model_id == NO_MODEL:
        raise SystemExit("no model configured: the owner sets model_id in configs/models.yaml before any live call")
    if os.environ.get("AGENT_SQL_ALLOW_LIVE") != "1":
        raise SystemExit("live LLM calls are disabled; the owner enables a run with AGENT_SQL_ALLOW_LIVE=1")
    if model.provider == "openai_compatible":
        from agent_sql.llm.openai_compatible import OpenAICompatibleClient

        return OpenAICompatibleClient(model.base_url, max_retries=model.sdk_max_retries)
    from agent_sql.llm.anthropic_client import AnthropicClient

    return AnthropicClient(max_retries=model.sdk_max_retries, interactive_fallbacks=interactive)
