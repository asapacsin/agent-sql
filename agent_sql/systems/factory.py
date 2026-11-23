"""Build a System from its config. The single place that maps config kinds to implementations."""
from __future__ import annotations

from agent_sql.config.settings import SystemConfig
from agent_sql.systems.base import Runtime


def build_system(rt: Runtime, cfg: SystemConfig):  # noqa: ANN201 - returns a System
    if cfg.kind == "b0":
        from agent_sql.systems.b0_oneshot import B0System

        return B0System(rt, cfg)
    if cfg.kind == "b1":
        from agent_sql.systems.b1_retrieval import B1System

        return B1System(rt, cfg)
    if cfg.kind == "b1_sc":
        from agent_sql.systems.b1_retrieval import B1SelfConsistency

        return B1SelfConsistency(rt, cfg)
    if cfg.kind == "a1":
        from agent_sql.systems.a1_agent import A1System

        return A1System(rt, cfg)
    if cfg.kind == "a2":
        from agent_sql.systems.a2_planner import A2System

        return A2System(rt, cfg)
    raise ValueError(f"unknown system kind {cfg.kind}")
