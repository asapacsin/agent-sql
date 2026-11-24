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

ACCESS_LOG = REPO_ROOT / "results" / "test_access_log.jsonl"


class DatasetRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: str  # bi | bird
    split: str
    limit: int | None = None
    task_types: list[str] | None = None  # filter (E3 uses aggregate/ranking/trend)
    fault: str | None = None  # E3: fm1 (transient error) | fm2 (schema drift + stale docs) | fm3 (value casing)


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


FAULT_DB = {"fm2": "retailco_test_drift", "fm3": "retailco_test_casing"}


def load_tasks(ref: DatasetRef) -> list[EvalTask]:
    import dataclasses

    if ref.dataset == "bi":
        tasks = load_bi_tasks(ref.split)
        if ref.task_types:
            tasks = [t for t in tasks if t.task_type in ref.task_types]
        if ref.fault in FAULT_DB:
            tasks = [dataclasses.replace(t, db_id=FAULT_DB[ref.fault]) for t in tasks]
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
        self.live = llm is None and mode == "record"  # a real provider will be called
        cache_path = Path(cfg.llm.cache) if cfg.llm.cache else self.out / "llm_cache.jsonl"
        if llm is not None and mode == "record":
            self.llm: LLMClient = CachedLLM(cache_path, "record", llm)
        elif mode == "replay":
            self.llm = CachedLLM(cache_path, "replay")
        else:
            from agent_sql.llm.factory import live_client

            self.llm = CachedLLM(cache_path, "record", live_client(self.model))
        self.mode = mode

    def db(self, db_id: str) -> Database:
        if db_id not in self.databases:
            self.databases[db_id] = open_database(db_id, self.cfg.bi_engine if db_id.startswith("retailco_") else None)
        return self.databases[db_id]

    def _guard(self, manifest: dict) -> None:
        held_out = any(ref.split in ("test", "study") for ref in self.cfg.datasets)
        if held_out and self.allow_dirty:
            raise SystemExit("--allow-dirty is never allowed for test/study splits")
        if manifest["dirty"] and not self.allow_dirty:
            raise SystemExit("refusing to run on a dirty git tree (use --allow-dirty for exploratory runs)")
        if held_out:
            self._check_frozen_snapshots()
        if self.live and (held_out or not self.cfg.exploratory) and not self.pricing.is_priced(self.model.model_id):
            raise SystemExit(f"model {self.model.model_id} is UNPRICED in configs/pricing.yaml; budget-capped "
                             "experiments need confirmed rates (exploratory dev runs are allowed)")
        for ref in self.cfg.datasets:
            if ref.dataset == "bi" and ref.split in ("test", "study"):
                from bi_dataset.verify_frozen import frozen_task_hash

                if frozen_task_hash(ref.split) != manifest_hash(ref.split):
                    raise SystemExit(f"BI {ref.split} manifest hash differs from the frozen value; refusing to run")
        if any(ref.split == "test" for ref in self.cfg.datasets):
            from agent_sql.eval.prereg import is_preregistered

            if not is_preregistered(manifest["config_hash"]):
                raise SystemExit("test split requires a preregistered config hash (experiments/preregistration.md)")

    def _check_frozen_snapshots(self) -> None:
        """Held-out data must be byte-identical to the frozen manifests (task files checked in _guard callers)."""
        import json as _json

        from agent_sql.config.settings import sha256_file

        frozen = _json.loads((REPO_ROOT / "data" / "manifests" / "frozen.json").read_text())
        for ref in self.cfg.datasets:
            if ref.dataset == "bird":
                from agent_sql.eval.datasets.bird import SPLIT_FILE

                if sha256_file(SPLIT_FILE) != frozen["bird_splits_sha256"]:
                    raise SystemExit("data/splits/bird.json differs from the frozen hash; refusing to run")
            if ref.dataset == "bi" and ref.split in ("test", "study"):
                from agent_sql.db.registry import bi_sqlite_path

                if sha256_file(bi_sqlite_path("test")) != frozen["retailco_test_agent_sqlite_sha256"]:
                    raise SystemExit("RetailCo-test database differs from the frozen snapshot; refusing to run")

    def run(self) -> RunOutcome:
        tasks = [t for ref in self.cfg.datasets for t in load_tasks(ref)]
        sys_cfgs = {sid: load_system(sid) for sid in self.cfg.systems}
        from agent_sql.systems.base import load_prompts

        prompts = {f"{sid}:{role}": p.sha256 for sid, c in sys_cfgs.items() for role, p in load_prompts(c.prompts).items()}
        # the preregistered hash binds configs, prompt TEXT and dataset manifests (reviewer finding)
        resolved = {"experiment": self.cfg.model_dump(), "systems": {k: v.model_dump() for k, v in sys_cfgs.items()},
                    "model": self.model.model_dump(), "limits": self.limits.model_dump(),
                    "datasets": dataset_hashes(self.cfg), "prompt_sha256": prompts}
        chash = config_hash(resolved)
        manifest = build_manifest(experiment_id=self.cfg.experiment_id, config_hash=chash, prompts=prompts,
                                  datasets=resolved["datasets"], model_id=self.model.model_id,
                                  pricing_version=self.pricing.version, seed=20261123,
                                  extra={"llm_mode": self.mode, "exploratory": self.cfg.exploratory or self.allow_dirty,
                                         "bi_engine": self.cfg.bi_engine})
        self._guard(manifest)
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "resolved_config.json").write_text(json.dumps(resolved, indent=2, default=str) + "\n")
        if any(ref.split == "test" for ref in self.cfg.datasets):
            ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with open(ACCESS_LOG, "a", encoding="utf-8") as f:
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

        fm1 = {t.task_id for ref in self.cfg.datasets if ref.fault == "fm1" for t in load_tasks(ref)}

        def work(item):  # noqa: ANN001, ANN202
            run_index, task, sid = item
            if state["stop"]:
                return None
            model = self.model if not sys_cfgs[sid].effort else self.model.model_copy(update={"effort": sys_cfgs[sid].effort})
            rt = Runtime(self.db(task.db_id), self.llm, model, self.limits, self.pricing, trace_dir,
                         fault_hook=transient_fault_once() if task.task_id in fm1 else None)
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
                if result.usage.cost_usd == result.usage.cost_usd:  # NaN (unpriced) never counts toward the cap
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


def transient_fault_once():  # noqa: ANN201
    """E3 FM1: the first execution of each task-run raises a transient DB error; later executions succeed."""
    import sqlite3

    state = {"fired": False}

    def hook(sql: str) -> None:
        if not state["fired"]:
            state["fired"] = True
            raise sqlite3.OperationalError("disk I/O error (injected transient fault)")

    return hook
