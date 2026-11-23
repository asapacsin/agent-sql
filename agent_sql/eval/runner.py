"""Experiment runner (docs/spec/EXPERIMENTS.md section 6)."""
from __future__ import annotations

import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from agent_sql.config.settings import (
    REPO_ROOT,
    config_hash,
    load_limits,
    load_model_settings,
    load_system,
    load_yaml,
)
from agent_sql.db.executor import Database
from agent_sql.db.registry import open_database
from agent_sql.eval.datasets.bi import EvalTask, load_bi_tasks, manifest_hash
from agent_sql.llm.base import LLMClient
from agent_sql.llm.cache import CachedLLM
from agent_sql.llm.pricing import load_pricing
from agent_sql.systems.base import Runtime
from agent_sql.systems.contracts import FinalAnswer, RunResult
from agent_sql.systems.factory import build_system
from agent_sql.tracing.manifest import build_manifest


class DatasetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str  # bi | bird
    split: str
    limit: int | None = None


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: str = "record"  # record | replay
    cache: str | None = None


class ExperimentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experiment_id: str
    datasets: list[DatasetRef]
    systems: list[str]
    runs: int = 1
    bi_engine: str = "sqlite"
    llm: LLMSettings = LLMSettings()
    concurrency: int = 4
    max_experiment_usd: float = 10.0
    output_dir: str | None = None
    exploratory: bool = False


def load_experiment(path: Path) -> ExperimentConfig:
    return ExperimentConfig(**load_yaml(path))


def load_tasks(ref: DatasetRef) -> list[EvalTask]:
    if ref.dataset == "bi":
        tasks = load_bi_tasks(ref.split)
    elif ref.dataset == "bird":
        from agent_sql.eval.datasets.bird import load_bird_tasks

        tasks = load_bird_tasks(ref.split)
    else:
        raise ValueError(ref.dataset)
    return tasks[: ref.limit] if ref.limit else tasks


def dataset_hashes(cfg: ExperimentConfig) -> dict[str, str]:
    out = {}
    for ref in cfg.datasets:
        if ref.dataset == "bi":
            out[f"bi:{ref.split}"] = manifest_hash(ref.split)
        else:
            from agent_sql.eval.datasets.bird import split_hash

            out[f"bird:{ref.split}"] = split_hash()
    return out


@dataclass
class RunOutcome:
    out_dir: Path
    completed: int
    total: int
    cost_usd: float
    incomplete: bool
    errors: list[str] = field(default_factory=list)


class Runner:
    def __init__(self, cfg: ExperimentConfig, *, llm: LLMClient | None = None, replay: bool = False,
                 allow_dirty: bool = False, out_dir: Path | None = None,
                 databases: dict[str, Database] | None = None) -> None:
        self.cfg = cfg
        self.out = Path(out_dir or cfg.output_dir or REPO_ROOT / "results" / cfg.experiment_id)
        self.model = load_model_settings()
        self.limits = load_limits()
        self.pricing = load_pricing()
        self.allow_dirty = allow_dirty
        self.databases = databases or {}
        mode = "replay" if replay else cfg.llm.mode
        cache_path = Path(cfg.llm.cache) if cfg.llm.cache else self.out / "llm_cache.jsonl"
        if llm is not None and mode == "record":
            self.llm: LLMClient = CachedLLM(cache_path, "record", llm)
        elif mode == "replay":
            self.llm = CachedLLM(cache_path, "replay")
        else:
            from agent_sql.llm.anthropic_client import AnthropicClient

            self.llm = CachedLLM(cache_path, "record", AnthropicClient(max_retries=self.model.sdk_max_retries))
        self.mode = mode

    def db(self, db_id: str) -> Database:
        if db_id not in self.databases:
            self.databases[db_id] = open_database(db_id, self.cfg.bi_engine if db_id.startswith("retailco_") else None)
        return self.databases[db_id]

    def _guard(self, manifest: dict) -> None:
        if manifest["dirty"] and not self.allow_dirty:
            raise SystemExit("refusing to run on a dirty git tree (use --allow-dirty for exploratory runs)")
        if any(ref.split == "test" for ref in self.cfg.datasets):
            from agent_sql.eval.prereg import is_preregistered

            if not is_preregistered(manifest["config_hash"]):
                raise SystemExit("test split requires a preregistered config hash (experiments/preregistration.md)")

    def run(self) -> RunOutcome:
        tasks = [t for ref in self.cfg.datasets for t in load_tasks(ref)]
        sys_cfgs = {sid: load_system(sid) for sid in self.cfg.systems}
        resolved = {"experiment": self.cfg.model_dump(), "systems": {k: v.model_dump() for k, v in sys_cfgs.items()},
                    "model": self.model.model_dump(), "limits": self.limits.model_dump(),
                    "datasets": dataset_hashes(self.cfg)}
        chash = config_hash(resolved)
        from agent_sql.systems.base import load_prompts

        prompts = {f"{sid}:{role}": p.sha256 for sid, c in sys_cfgs.items() for role, p in load_prompts(c.prompts).items()}
        manifest = build_manifest(experiment_id=self.cfg.experiment_id, config_hash=chash, prompts=prompts,
                                  datasets=resolved["datasets"], model_id=self.model.model_id,
                                  pricing_version=self.pricing.version, seed=20261123,
                                  extra={"llm_mode": self.mode, "exploratory": self.cfg.exploratory or self.allow_dirty,
                                         "bi_engine": self.cfg.bi_engine})
        self._guard(manifest)
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "resolved_config.json").write_text(json.dumps(resolved, indent=2, default=str) + "\n")
        if any(ref.split == "test" for ref in self.cfg.datasets):
            with open(REPO_ROOT / "results" / "test_access_log.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps({"experiment_id": self.cfg.experiment_id, "config_hash": chash,
                                    "at": datetime.now(UTC).isoformat()}) + "\n")
        pred_path = self.out / "predictions.jsonl"
        done = set()
        if pred_path.exists():  # resume: never re-run completed (run, task, system)
            for line in pred_path.read_text().splitlines():
                r = json.loads(line)
                done.add((r["run_index"], r["task_id"], r["system_id"]))
        schedule = []
        for run_index in range(self.cfg.runs):
            pairs = [(run_index, t, sid) for t in tasks for sid in self.cfg.systems]
            random.Random(20261123 + run_index).shuffle(pairs)
            schedule += [p for p in pairs if (p[0], p[1].task_id, p[2]) not in done]
        lock = threading.Lock()
        state = {"cost": 0.0, "stop": False, "completed": 0}
        trace_dir = self.out / "traces"
        errors: list[str] = []

        def work(item):  # noqa: ANN001, ANN202
            run_index, task, sid = item
            if state["stop"]:
                return None
            rt = Runtime(self.db(task.db_id), self.llm, self.model, self.limits, self.pricing, trace_dir)
            system = build_system(rt, sys_cfgs[sid])
            try:
                result = system.run(task.to_input())
            except Exception as e:  # noqa: BLE001 - a crashing system is a recorded failure, never a silent skip
                errors.append(f"{sid}/{task.task_id}: {type(e).__name__}: {e}")
                result = RunResult(task_id=task.task_id, system_id=sid, run_id="crash",
                                   answer=FinalAnswer.failure("FAILED_INVALID_OUTPUT",
                                                              f"system crashed: {type(e).__name__}: {e}"))
            with lock:
                with open(pred_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"run_index": run_index, "task_id": task.task_id, "system_id": sid,
                                        "result": result.model_dump(mode="json")}, sort_keys=True) + "\n")
                state["cost"] += result.usage.cost_usd
                state["completed"] += 1
                if state["cost"] >= self.cfg.max_experiment_usd:
                    state["stop"] = True
            return result

        with ThreadPoolExecutor(max_workers=max(1, self.cfg.concurrency)) as pool:
            for fut in as_completed([pool.submit(work, it) for it in schedule]):
                fut.result()
        incomplete = state["completed"] < len(schedule)
        manifest.update({"finished_at": datetime.now(UTC).isoformat(), "config_hash": chash,
                         "scheduled": len(schedule) + len(done), "completed": state["completed"] + len(done),
                         "incomplete": incomplete, "cost_usd": round(state["cost"], 6), "errors": errors})
        (self.out / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str) + "\n")
        return RunOutcome(self.out, state["completed"], len(schedule), state["cost"], incomplete, errors)


def read_predictions(run_dir: Path) -> list[tuple[int, RunResult]]:
    out = []
    for line in (run_dir / "predictions.jsonl").read_text().splitlines():
        r = json.loads(line)
        out.append((r["run_index"], RunResult(**r["result"])))
    return out


def load_experiment_from_dir(run_dir: Path) -> ExperimentConfig:
    resolved = json.loads((Path(run_dir) / "resolved_config.json").read_text())
    return ExperimentConfig(**resolved["experiment"])
