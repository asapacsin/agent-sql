"""Every system / ablation config runs end-to-end through runner, scorer and report with FakeLLM (Phase 4 gate)."""
from __future__ import annotations

from pathlib import Path

import pytest

from agent_sql.config.settings import REPO_ROOT, load_system
from agent_sql.eval.cli import score_run
from agent_sql.eval.datasets import bird
from agent_sql.eval.report import load_scores, render
from agent_sql.eval.runner import DatasetRef, ExperimentConfig, Runner, load_experiment
from agent_sql.llm.fake import FakeLLM
from tests.fake_policy import policy

ALL_SYSTEMS = sorted(p.stem for p in (REPO_ROOT / "configs" / "systems").glob("*.yaml"))


def test_every_experiment_config_parses_and_references_known_systems():
    for path in sorted((REPO_ROOT / "configs" / "experiments").glob("*.yaml")):
        cfg = load_experiment(path)
        for sid in cfg.systems:
            load_system(sid)


def test_all_systems_run_end_to_end(tmp_path: Path, bi_dev_sqlite):
    datasets = [DatasetRef(dataset="bi", split="dev", limit=2)]
    if bird.DEV_JSON.exists():
        datasets.append(DatasetRef(dataset="bird", split="dev", limit=1))
    cfg = ExperimentConfig(experiment_id="ablations_it", datasets=datasets, systems=ALL_SYSTEMS, runs=1,
                           concurrency=4)
    out = Runner(cfg, llm=FakeLLM(policy, model="claude-opus-5-5"), allow_dirty=True, out_dir=tmp_path,
                 databases={"retailco_dev": bi_dev_sqlite}).run()
    assert not out.errors, out.errors
    score_run(tmp_path)
    scores = load_scores(tmp_path / "scores.jsonl")
    assert {s["system_id"] for s in scores} == set(ALL_SYSTEMS)
    assert all(s["status"] != "FAILED_INVALID_OUTPUT" for s in scores), \
        [(s["system_id"], s["task_id"], s["detail"]) for s in scores if s["status"] == "FAILED_INVALID_OUTPUT"]
    md, _ = render(scores, None)
    assert "## Ablations" in md and "AB7" in md


@pytest.mark.parametrize("sid", ["a2", "a2_noplan", "a2_noverify"])
def test_a2_variants_produce_answers(sid, bi_dev_sqlite, tmp_path):
    cfg = ExperimentConfig(experiment_id=sid, datasets=[DatasetRef(dataset="bi", split="dev", limit=1)],
                           systems=[sid], runs=1, concurrency=1)
    Runner(cfg, llm=FakeLLM(policy, model="claude-opus-5-5"), allow_dirty=True, out_dir=tmp_path,
           databases={"retailco_dev": bi_dev_sqlite}).run()
    import json

    pred = json.loads((tmp_path / "predictions.jsonl").read_text().splitlines()[0])
    assert pred["result"]["answer"]["status"] == "ANSWERED"
