"""Metric registry loaded from metrics.yaml; renders the shared glossary text."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

METRICS_PATH = Path(__file__).with_name("metrics.yaml")


@dataclass(frozen=True)
class Metric:
    id: str
    name: str
    aliases: tuple[str, ...]
    unit: str
    definition: str
    formula: str
    required_tables: tuple[str, ...]
    required_predicates: tuple[str, ...]
    template: str | None

    def render_template(self, start: str, end: str) -> str:
        if not self.template:
            raise ValueError(f"metric {self.id} has no canonical template")
        return self.template.replace("{start}", start).replace("{end}", end)


@dataclass(frozen=True)
class Registry:
    metrics: dict[str, Metric]
    recognized_lines: str
    attribution: dict[str, str]

    def get(self, metric_id: str) -> Metric:
        return self.metrics[metric_id]

    def __contains__(self, metric_id: str) -> bool:
        return metric_id in self.metrics

    def glossary(self) -> str:
        """Natural-language definitions only (never SQL templates): identical context for every system."""
        lines = ["Metric glossary (canonical definitions):", f"- RL: {self.recognized_lines}"]
        for m in self.metrics.values():
            alias = f" (aliases: {', '.join(m.aliases)})" if m.aliases else ""
            lines.append(f"- {m.id}{alias}: {m.definition} Formula: {m.formula}.")
        lines.append("Attribution rules:")
        lines += [f"- {k}: {v}" for k, v in self.attribution.items()]
        return "\n".join(lines)


@lru_cache(maxsize=1)
def load_registry(path: Path = METRICS_PATH) -> Registry:
    data = yaml.safe_load(path.read_text())
    metrics = {}
    for m in data["metrics"]:
        metrics[m["id"]] = Metric(m["id"], m["name"], tuple(m.get("aliases", [])), m["unit"], m["definition"],
                                  str(m["formula"]), tuple(m.get("required_tables", [])),
                                  tuple(m.get("required_predicates", [])), m.get("template"))
    return Registry(metrics, data["recognized_lines"], data["attribution"])
