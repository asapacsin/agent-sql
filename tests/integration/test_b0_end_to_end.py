"""B0 end-to-end with FakeLLM through the real runner, scorer and report (no model claims)."""
from __future__ import annotations

import json

from agent_sql.config.settings import load_limits, load_model_settings, load_system
from agent_sql.eval.cli import score_run
from agent_sql.eval.report import load_scores, render, write_report
from agent_sql.eval.runner import DatasetRef, ExperimentConfig, Runner
from agent_sql.llm.fake import FakeLLM
from agent_sql.llm.pricing import load_pricing
from agent_sql.systems.b0_oneshot import B0System
from agent_sql.systems.base import Runtime
from agent_sql.systems.contracts import TaskInput

GOOD_SQL = ("SELECT COUNT(DISTINCT o.order_id) AS value FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
            "WHERE o.status IN ('shipped','completed') AND c.is_test_account = FALSE "
            "AND o.order_date >= '2025-12-01' AND o.order_date < '2026-01-01'")


def composer_json(value, column="value"):
    return json.dumps({"answer_text": "answer", "claims": [{"text": "orders", "value": value, "unit": "count",
                                                            "entities": [], "row_index": 0, "column": column}],
                       "metric_interpretations": [], "assumptions": [], "limitations": [],
                       "uncertainty": {"level": "low", "reasons": []}, "chart": None})


def script(req, i):
    if req.output_schema and "sql" in req.output_schema["properties"]:
        return json.dumps({"sql": GOOD_SQL})
    return composer_json(2367)


def _rt(db, llm):
    return Runtime(db, llm, load_model_settings(), load_limits(), load_pricing())


def test_b0_execution_failure_produces_no_numbers(bi_dev_sqlite):
    llm = FakeLLM([json.dumps({"sql": "SELECT no_such_col FROM orders"})], model="claude-opus-5-5")
    r = B0System(_rt(bi_dev_sqlite, llm), load_system("b0")).run(
        TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r.answer.status == "FAILED_EXECUTION" and r.answer.claims == []
    assert len(llm.calls) == 1, "B0 never retries and never calls the composer after a failure"


def test_b0_refuses_unsafe_sql(bi_dev_sqlite):
    llm = FakeLLM([json.dumps({"sql": "SELECT 1; DROP TABLE orders"})], model="claude-opus-5-5")
    r = B0System(_rt(bi_dev_sqlite, llm), load_system("b0")).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r.answer.status == "FAILED_EXECUTION" and r.queries[0]["status"] == "REJECTED"


def test_model_mismatch_fails_closed(bi_dev_sqlite):
    llm = FakeLLM([json.dumps({"sql": GOOD_SQL})], model="claude-opus-5-5", served_model="some-other-model")
    r = B0System(_rt(bi_dev_sqlite, llm), load_system("b0")).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r.answer.status == "FAILED_MODEL_MISMATCH"


def test_runner_score_report_pipeline(tmp_path, bi_dev_sqlite):
    cfg = ExperimentConfig(experiment_id="it", datasets=[DatasetRef(dataset="bi", split="dev")], systems=["b0"],
                           runs=2, concurrency=4)
    out = Runner(cfg, llm=FakeLLM(script, model="claude-opus-5-5"), allow_dirty=True, out_dir=tmp_path,
                 databases={"retailco_dev": bi_dev_sqlite}).run()
    assert out.completed == 40 and not out.errors
    score_run(tmp_path)
    scores = load_scores(tmp_path / "scores.jsonl")
    by_task = {s["task_id"]: s for s in scores if s["run_index"] == 0}
    assert by_task["bi-dev-002"]["success"] and by_task["bi-dev-002"]["correct"]
    assert not by_task["bi-dev-001"]["success"] and by_task["bi-dev-001"]["executed"]
    assert by_task["bi-dev-018"]["failure_code"] == "F-FABRICATION"  # answered an insufficient-data task
    report = write_report(tmp_path)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert render(scores, manifest)[0] == report.read_text(), "report must be a pure function of scores"
    trace_files = list((tmp_path / "traces").glob("*.jsonl"))
    assert len(trace_files) == 40
    events = [json.loads(line) for line in trace_files[0].read_text().splitlines()]
    assert {"run_start", "llm_call", "tool_call", "tool_result", "final"} <= {e["type"] for e in events}
