"""Cost from provider-reported usage and the versioned pricing table (configs/pricing.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_sql.config.settings import REPO_ROOT, load_yaml
from agent_sql.llm.base import Usage


@dataclass(frozen=True)
class Rates:
    input: float | None
    output: float | None
    cache_read: float | None
    cache_write: float | None


@dataclass(frozen=True)
class PricingTable:
    version: str
    models: dict[str, Rates]

    def rates(self, model: str) -> Rates:
        if model not in self.models:
            raise KeyError(f"no pricing for model {model!r} in pricing version {self.version}")
        return self.models[model]

    def is_priced(self, model: str) -> bool:
        return model in self.models and self.models[model].input is not None

    def cost(self, model: str, usage: Usage) -> float:
        """USD cost; NaN when the model is listed but its rates are not yet confirmed (reported as UNPRICED)."""
        if not self.is_priced(model):
            if model in self.models:
                return float("nan")
            raise KeyError(f"no pricing for model {model!r} in pricing version {self.version}")
        r = self.rates(model)
        return (usage.input_tokens * r.input + usage.cache_read_input_tokens * r.cache_read
                + usage.cache_creation_input_tokens * r.cache_write + usage.output_tokens * r.output) / 1e6

    def uncached_cost(self, model: str, usage: Usage) -> float:
        if not self.is_priced(model):
            return self.cost(model, usage)
        r = self.rates(model)
        return (usage.total_input * r.input + usage.output_tokens * r.output) / 1e6


def load_pricing(path: Path | None = None) -> PricingTable:
    data = load_yaml(path or REPO_ROOT / "configs" / "pricing.yaml")
    models = {m: Rates(v.get("input"), v.get("output"), v.get("cache_read"), v.get("cache_write"))
              for m, v in data["models"].items()}
    models.setdefault("fake-model", Rates(0.0, 0.0, 0.0, 0.0))
    return PricingTable(str(data["version"]), models)
