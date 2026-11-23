"""Cost from provider-reported usage and the versioned pricing table (configs/pricing.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_sql.config.settings import REPO_ROOT, load_yaml
from agent_sql.llm.base import Usage


@dataclass(frozen=True)
class Rates:
    input: float
    output: float
    cache_read: float
    cache_write: float


@dataclass(frozen=True)
class PricingTable:
    version: str
    models: dict[str, Rates]

    def rates(self, model: str) -> Rates:
        if model not in self.models:
            raise KeyError(f"no pricing for model {model!r} in pricing version {self.version}")
        return self.models[model]

    def cost(self, model: str, usage: Usage) -> float:
        r = self.rates(model)
        return (usage.input_tokens * r.input + usage.cache_read_input_tokens * r.cache_read
                + usage.cache_creation_input_tokens * r.cache_write + usage.output_tokens * r.output) / 1e6

    def uncached_cost(self, model: str, usage: Usage) -> float:
        r = self.rates(model)
        return (usage.total_input * r.input + usage.output_tokens * r.output) / 1e6


def load_pricing(path: Path | None = None) -> PricingTable:
    data = load_yaml(path or REPO_ROOT / "configs" / "pricing.yaml")
    models = {m: Rates(**v) for m, v in data["models"].items()}
    models.setdefault("fake-model", Rates(0.0, 0.0, 0.0, 0.0))
    return PricingTable(str(data["version"]), models)
