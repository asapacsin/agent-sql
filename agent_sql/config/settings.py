"""Configuration models, canonical hashing, and loading.

All tunable numbers come from configs/*.yaml, which must equal docs/spec/parameters.json
(test: tests/unit/test_config.py).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

REPO_ROOT = Path(__file__).resolve().parents[2]


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelSettings(Strict):
    model_id: str
    effort: Literal["low", "medium", "high", "xhigh", "max"]
    max_tokens_per_call: int = Field(gt=0)
    benchmark_mode: bool = True  # fallbacks disabled, served model must equal model_id
    sdk_max_retries: int = 2


class ExecutionLimits(Strict):
    statement_timeout_s: float = Field(gt=0)
    task_wall_clock_s: float = Field(gt=0)
    max_result_rows_stored: int = Field(gt=0)
    observation_max_rows: int = Field(gt=0)
    observation_max_cell_chars: int = Field(gt=0)


class EvalSettings(Strict):
    eval_reexec_timeout_s: float = Field(gt=0)
    eval_reexec_max_rows: int = Field(gt=0)
    runs_per_config: int = Field(gt=0)
    bootstrap_resamples: int = Field(gt=0)
    seed: int
    rel_tolerance: float
    abs_tolerance: float
    bird_test_size: int
    bird_val_size: int
    bi_test_size: int


class AgentBudgets(Strict):
    max_llm_calls: int
    max_tool_calls: int
    max_input_tokens: int
    max_repair_attempts: int
    final_answer_reserve_tokens: int
    max_failures_per_task: int | None = None
    max_plan_steps: int | None = None
    max_replans: int | None = None
    max_verification_queries: int | None = None


class SystemConfig(Strict):
    system_id: str
    kind: Literal["b0", "b1", "b1_sc", "a1", "a2"]
    prompts: dict[str, str]  # role -> prompt path relative to prompts/
    b1_top_k_tables: int | None = None
    budgets: AgentBudgets | None = None
    flags: dict[str, Any] = {}


def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def configs_dir(root: Path = REPO_ROOT) -> Path:
    return root / "configs"


def load_model_settings(root: Path = REPO_ROOT) -> ModelSettings:
    return ModelSettings(**load_yaml(configs_dir(root) / "models.yaml"))


def load_limits(root: Path = REPO_ROOT) -> ExecutionLimits:
    return ExecutionLimits(**load_yaml(configs_dir(root) / "limits.yaml"))


def load_eval_settings(root: Path = REPO_ROOT) -> EvalSettings:
    return EvalSettings(**load_yaml(configs_dir(root) / "eval.yaml"))


def load_system(system_id: str, root: Path = REPO_ROOT) -> SystemConfig:
    return SystemConfig(**load_yaml(configs_dir(root) / "systems" / f"{system_id}.yaml"))


def flatten_parameters(root: Path = REPO_ROOT) -> dict[str, Any]:
    """Project configs/ back onto the canonical parameter names of docs/spec/parameters.json."""
    m, lim, ev = load_model_settings(root), load_limits(root), load_eval_settings(root)
    out: dict[str, Any] = {"model_id": m.model_id, "effort": m.effort, "max_tokens_per_call": m.max_tokens_per_call}
    out.update(lim.model_dump())
    out.update({k: v for k, v in ev.model_dump().items()})
    b1 = load_system("b1", root)
    out["b1_top_k_tables"] = b1.b1_top_k_tables
    for sid in ("a1", "a2"):
        path = configs_dir(root) / "systems" / f"{sid}.yaml"
        if not path.exists():
            continue
        b = load_system(sid, root).budgets
        assert b is not None
        out["max_repair_attempts"] = b.max_repair_attempts
        out["final_answer_reserve_tokens"] = b.final_answer_reserve_tokens
        out[f"{sid}_max_llm_calls"] = b.max_llm_calls
        out[f"{sid}_max_tool_calls"] = b.max_tool_calls
        out[f"{sid}_max_input_tokens"] = b.max_input_tokens
        if sid == "a2":
            out["a2_max_failures_per_task"] = b.max_failures_per_task
            out["a2_max_plan_steps"] = b.max_plan_steps
            out["a2_max_replans"] = b.max_replans
            out["a2_max_verification_queries"] = b.max_verification_queries
    return out


def config_hash(obj: BaseModel | dict) -> str:
    data = obj.model_dump() if isinstance(obj, BaseModel) else obj
    return sha256_text(canonical_json(data))
