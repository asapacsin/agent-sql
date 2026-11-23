"""B1 / B1-SC end-to-end with FakeLLM on BI and BIRD; frozen-manifest guard (Phase 2)."""
from __future__ import annotations

import json

import pytest

from agent_sql.config.settings import SystemConfig, load_limits, load_model_settings, load_system
from agent_sql.eval.cli import score_run
from agent_sql.eval.datasets import bird
from agent_sql.eval.report import load_scores
from agent_sql.eval.runner import DatasetRef, ExperimentConfig, Runner
from agent_sql.llm.fake import FakeLLM
from agent_sql.llm.pricing import load_pricing
from agent_sql.systems.b1_retrieval import B1SelfConsistency, B1System
from agent_sql.systems.base import Runtime
from agent_sql.systems.contracts import TaskInput


def _rt(db, llm):
    return Runtime(db, llm, load_model_settings(), load_limits(), load_pricing())


def test_b1_uses_retrieved_context(bi_dev_sqlite):
    llm = FakeLLM([json.dumps({"sql": "SELECT COUNT(*) AS n FROM orders"}),
                   json.dumps({"answer_text": "a", "claims": [], "metric_interpretations": [], "assumptions": [],
                               "limitations": [], "uncertainty": {"level": "low", "reasons": []}, "chart": None})],
                  model="claude-opus-5-5")
    r = B1System(_rt(bi_dev_sqlite, llm), load_system("b1")).run(
        TaskInput(task_id="t", db_id="retailco_dev", question="How many orders by product category?"))
    assert r.answer.status == "ANSWERED" and r.retrieved_tables and "categories" in r.retrieved_tables
    assert r.counters.tool_calls == 2 and "Relevant schema (retrieved" in llm.calls[0].system
    assert "TABLE promotions" not in llm.calls[0].system or "promotions" in r.retrieved_tables


def test_b1_sc_majority_vote(bi_dev_sqlite):
    sqls = ["SELECT 1 AS v", "SELECT 2 AS v", "SELECT 2 AS v", "SELECT nope FROM orders", "SELECT 1 AS v"]
    seq = iter(sqls)
    llm = FakeLLM(lambda req, i: json.dumps({"sql": next(seq)}), model="claude-opus-5-5")
    cfg = SystemConfig(system_id="b1_sc", kind="b1_sc", prompts={"sql": "b1/sql.v1.md"}, b1_top_k_tables=5,
                       flags={"samples": 5})
    r = B1SelfConsistency(_rt(bi_dev_sqlite, llm), cfg).run(TaskInput(task_id="t", db_id="bird:x", question="q"))
    assert r.answer.status == "ANSWERED"
    final = next(q for q in r.queries if q["query_id"] == r.answer.final_query_id)
    assert final["sql"] == "SELECT 1 AS v"  # tie 2-2 broken by lowest candidate index


@pytest.mark.skipif(not bird.DEV_JSON.exists(), reason="BIRD not downloaded")
def test_bird_pipeline_gold_oracle_is_not_used_by_systems(tmp_path):
    """Run B0 on 5 BIRD dev tasks with a fake that returns a fixed query; scoring uses EX semantics."""
    llm = FakeLLM(lambda req, i: json.dumps({"sql": "SELECT 1"}), model="claude-opus-5-5")
    cfg = ExperimentConfig(experiment_id="bird_it", datasets=[DatasetRef(dataset="bird", split="dev", limit=5)],
                           systems=["b0", "b1"], runs=1, concurrency=2)
    out = Runner(cfg, llm=llm, allow_dirty=True, out_dir=tmp_path).run()
    assert out.completed == 10 and not out.errors
    score_run(tmp_path)
    scores = load_scores(tmp_path / "scores.jsonl")
    assert all(s["executed"] and not s["correct"] for s in scores)
    assert all(s["retrieval_recall"] is not None for s in scores if s["system_id"] == "b1")
    for call in llm.calls:
        assert "SELECT" not in call.messages[0]["content"]  # the user turn is the question only


@pytest.fixture
def clean_tree(monkeypatch):
    import agent_sql.tracing.manifest as m

    monkeypatch.setattr(m, "git_state", lambda root=None: {"git_sha": "test", "dirty": False})


def test_runner_refuses_allow_dirty_on_test(tmp_path):
    cfg = ExperimentConfig(experiment_id="g0", datasets=[DatasetRef(dataset="bi", split="test")], systems=["b0"])
    with pytest.raises(SystemExit, match="allow-dirty"):
        Runner(cfg, llm=FakeLLM([]), allow_dirty=True, out_dir=tmp_path).run()


def test_runner_refuses_modified_frozen_test_manifest(tmp_path, monkeypatch, clean_tree):
    import agent_sql.eval.runner as runner

    monkeypatch.setattr(runner, "manifest_hash", lambda split: "0" * 64)
    cfg = ExperimentConfig(experiment_id="guard", datasets=[DatasetRef(dataset="bi", split="test")], systems=["b0"])
    with pytest.raises(SystemExit, match="frozen"):
        Runner(cfg, llm=FakeLLM([]), out_dir=tmp_path).run()


def test_runner_refuses_unpreregistered_test_run(tmp_path, clean_tree):
    cfg = ExperimentConfig(experiment_id="guard2", datasets=[DatasetRef(dataset="bi", split="test")], systems=["b0"])
    with pytest.raises(SystemExit, match="preregistered"):
        Runner(cfg, llm=FakeLLM([]), out_dir=tmp_path).run()


def test_preregistered_test_run_writes_access_log(tmp_path, monkeypatch, clean_tree):
    import agent_sql.eval.prereg as prereg
    import agent_sql.eval.runner as runner
    from tests.fake_policy import policy

    monkeypatch.setattr(prereg, "is_preregistered", lambda h, path=None: True)
    log = tmp_path / "access.jsonl"
    monkeypatch.setattr(runner, "ACCESS_LOG", log)
    cfg = ExperimentConfig(experiment_id="acc", datasets=[DatasetRef(dataset="bi", split="test", limit=1)],
                           systems=["b0"])
    Runner(cfg, llm=FakeLLM(policy, model="claude-opus-5-5"), out_dir=tmp_path / "out").run()
    entries = [json.loads(line) for line in log.read_text().splitlines()]
    assert entries and entries[0]["experiment_id"] == "acc"


def test_config_hash_binds_prompt_text(tmp_path, monkeypatch, bi_dev_sqlite):
    import agent_sql.config.prompts as prompts
    from tests.fake_policy import policy

    cfg = ExperimentConfig(experiment_id="h", datasets=[DatasetRef(dataset="bi", split="dev", limit=1)],
                           systems=["b0"])
    Runner(cfg, llm=FakeLLM(policy, model="claude-opus-5-5"), allow_dirty=True, out_dir=tmp_path / "a",
           databases={"retailco_dev": bi_dev_sqlite}).run()
    original = prompts.load_prompt

    def edited(rel, root=prompts.REPO_ROOT):  # noqa: ANN001, ANN202
        p = original(rel, root)
        return prompts.Prompt(p.path, p.text + " edited", prompts.sha256_text(p.text + " edited"))

    monkeypatch.setattr(prompts, "load_prompt", edited)
    Runner(cfg, llm=FakeLLM(policy, model="claude-opus-5-5"), allow_dirty=True, out_dir=tmp_path / "b",
           databases={"retailco_dev": bi_dev_sqlite}).run()
    ha = json.loads((tmp_path / "a" / "manifest.json").read_text())["config_hash"]
    hb = json.loads((tmp_path / "b" / "manifest.json").read_text())["config_hash"]
    assert ha != hb
