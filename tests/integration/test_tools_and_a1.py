"""Data tools on SQLite and PostgreSQL; A1 flags; fault injection (Phase 3)."""
from __future__ import annotations

import pytest

from agent_sql.config.settings import SystemConfig, load_limits, load_model_settings, load_system
from agent_sql.db.registry import open_database
from agent_sql.eval.runner import transient_fault_once
from agent_sql.llm.fake import FakeLLM, tool_call
from agent_sql.llm.pricing import load_pricing
from agent_sql.systems.a1_agent import A1System
from agent_sql.systems.b0_oneshot import B0System
from agent_sql.systems.base import RunContext, Runtime
from agent_sql.systems.contracts import TaskInput
from agent_sql.tools.sql_tools import DATA_TOOLS


def ctx_for(db, llm=None):
    rt = Runtime(db, llm or FakeLLM([]), load_model_settings(), load_limits(), load_pricing())
    return RunContext(rt, TaskInput(task_id="t", db_id=db.db_id, question="q"), "a1")


def call(ctx, name, **args):
    return DATA_TOOLS[name].fn(ctx, purpose="test", **args)


def test_all_tools(bi_dev_any):
    ctx = ctx_for(bi_dev_any)
    assert {t["name"] for t in call(ctx, "list_tables", name_filter=None)["tables"]} >= {"orders", "customers"}
    d = call(ctx, "describe_table", table="customers")
    assert any(c["restricted"] for c in d["columns"]) and d["status"] == "OK"
    assert call(ctx, "describe_table", table="ordrs")["hint"].startswith("did you mean")
    rel = call(ctx, "inspect_relationships", tables=["order_items", "regions"])
    assert rel["paths"] and rel["paths"][0]["tables"][0] == "order_items"
    s1 = call(ctx, "sample_rows", table="orders", columns=["order_id", "status"], n=3)
    s2 = call(ctx, "sample_rows", table="orders", columns=["order_id", "status"], n=3)
    assert s1 == s2 and len(s1["rows"]) == 3
    assert call(ctx, "sample_rows", table="customers", columns=["email"], n=1)["error_class"] == "RESTRICTED_COLUMN"
    assert call(ctx, "validate_sql", sql="SELECT order_id FROM orders")["status"] == "OK"
    assert call(ctx, "validate_sql", sql="DELETE FROM orders")["status"] == "REJECTED"
    a = call(ctx, "execute_readonly_sql", sql="SELECT status, COUNT(*) AS n FROM orders GROUP BY status", label="by")
    b = call(ctx, "execute_readonly_sql", sql="SELECT COUNT(*) AS n FROM orders", label="tot")
    assert a["status"] == "OK" and a["query_id"] == "q1" and b["query_id"] == "q2"
    meta = call(ctx, "get_query_result_metadata", query_id="q1", columns=None)
    assert meta["row_count"] == 4 and meta["duplicate_rows"] == 0
    agg = call(ctx, "compare_query_results", left_query_id="q1", right_query_id="q2", mode="aggregate",
               key_columns=None, value_columns=[["n", "n"]], aggregate="sum", rel_tolerance=None)
    assert agg["within_tolerance"] is True
    rows = call(ctx, "compare_query_results", left_query_id="q1", right_query_id="q1", mode="rows",
                key_columns=["status"], value_columns=[["n", "n"]], aggregate="none", rel_tolerance=None)
    assert rows["matched"] == 4 and not rows["mismatched"]


def _answer(qid="q1"):
    return {"status": "ANSWERED", "answer_text": "a", "final_query_id": qid, "claims": [], "charts": [],
            "metric_interpretations": [], "assumptions": [], "limitations": [],
            "uncertainty": {"level": "low", "reasons": []}}


def _rt(db, llm):
    return Runtime(db, llm, load_model_settings(), load_limits(), load_pricing())


def test_flags_change_only_their_behaviour(bi_dev_sqlite):
    base = load_system("a1")
    off = SystemConfig(**{**base.model_dump(), "flags": {**base.flags, "exploration_tools": "off"}})
    llm = FakeLLM([tool_call("list_tables", {"purpose": "p", "name_filter": None}, "t1"),
                   tool_call("execute_readonly_sql", {"purpose": "p", "sql": "SELECT 1 AS x", "label": None}, "t2"),
                   tool_call("submit_answer", _answer(), "t3")], model="claude-opus-5-5")
    r = A1System(_rt(bi_dev_sqlite, llm), off).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r.answer.status == "ANSWERED"
    assert "list_tables" not in {t.name for t in llm.calls[0].tools}
    full = SystemConfig(**{**base.model_dump(), "flags": {**base.flags, "schema_context": "full"}})
    llm2 = FakeLLM([tool_call("execute_readonly_sql", {"purpose": "p", "sql": "SELECT 1 AS x", "label": None}, "t1"),
                    tool_call("submit_answer", _answer(), "t2")], model="claude-opus-5-5")
    r2 = A1System(_rt(bi_dev_sqlite, llm2), full).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r2.retrieved_tables is None and "TABLE promotions" in llm2.calls[0].system
    binary = SystemConfig(**{**base.model_dump(), "flags": {**base.flags, "execution_feedback": "binary"}})
    llm3 = FakeLLM([tool_call("execute_readonly_sql", {"purpose": "p", "sql": "SELECT nope FROM orders",
                                                       "label": None}, "t1"),
                    tool_call("submit_answer", {**_answer(), "status": "INSUFFICIENT_DATA", "final_query_id": None},
                              "t2")], model="claude-opus-5-5")
    A1System(_rt(bi_dev_sqlite, llm3), binary).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    observation = llm3.calls[1].messages[-1]["content"][0]["content"]
    assert "message" not in observation and "hint" not in observation
    zero = SystemConfig(**{**base.model_dump(), "flags": {**base.flags, "repair_budget": 0}})
    llm4 = FakeLLM([tool_call("execute_readonly_sql", {"purpose": "p", "sql": "SELECT nope FROM orders",
                                                       "label": None}, "t1")], model="claude-opus-5-5")
    r4 = A1System(_rt(bi_dev_sqlite, llm4), zero).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r4.answer.status == "FAILED_REPAIR_BUDGET"


def test_fm1_transient_fault_b0_fails_a1_can_recover(bi_dev_sqlite):
    import json

    sql = "SELECT COUNT(*) AS n FROM orders"
    rt = _rt(bi_dev_sqlite, FakeLLM([json.dumps({"sql": sql})], model="claude-opus-5-5"))
    rt.fault_hook = transient_fault_once()
    assert B0System(rt, load_system("b0")).run(TaskInput(task_id="t", db_id="bird:x", question="q")).answer.status \
        == "FAILED_EXECUTION"
    llm = FakeLLM([tool_call("execute_readonly_sql", {"purpose": "p", "sql": sql, "label": None}, "t1"),
                   tool_call("execute_readonly_sql", {"purpose": "retry", "sql": sql, "label": None}, "t2"),
                   tool_call("submit_answer", _answer("q2"), "t3")], model="claude-opus-5-5")
    rt2 = _rt(bi_dev_sqlite, llm)
    rt2.fault_hook = transient_fault_once()
    r = A1System(rt2, load_system("a1")).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r.answer.status == "ANSWERED" and [q["status"] for q in r.queries] == ["ERROR", "OK"]


@pytest.mark.skipif(not __import__("bi_dataset.variants", fromlist=["x"]).variant_dir().exists(),
                    reason="variants not built")
def test_fm2_drift_docs_are_stale_but_tools_show_truth():
    db = open_database("retailco_test_drift")
    ctx = ctx_for(db)
    from agent_sql.systems.base import render_full_schema

    assert "order_date" in render_full_schema(db) and "ordered_on" not in render_full_schema(db)
    cols = [c["name"] for c in call(ctx, "describe_table", table="orders")["columns"]]
    assert "ordered_on" in cols and "order_date" not in cols
    assert call(ctx, "execute_readonly_sql", sql="SELECT MIN(order_date) FROM orders", label=None)["error_class"] \
        == "UNKNOWN_COLUMN"


def test_tool_exception_becomes_observation(bi_dev_sqlite):
    llm = FakeLLM([tool_call("execute_readonly_sql", {"purpose": "p", "sql": "SELECT status FROM orders", "label": None}, "t1"),
                   tool_call("compare_query_results", {"purpose": "p", "left_query_id": "q1", "right_query_id": "q1",
                                                       "mode": "aggregate", "key_columns": None,
                                                       "value_columns": [["status", "status"]], "aggregate": "sum",
                                                       "rel_tolerance": None}, "t2"),
                   tool_call("complete_step", {"step_id": "s1", "query_id": "q1", "summary": "s",
                                               "unanswerable_reason": None}, "t3"),
                   tool_call("submit_answer", _answer(), "t4")], model="claude-opus-5-5")
    from agent_sql.systems.a2_planner import A2System

    cfg = load_system("a2")
    noplan = SystemConfig(**{**cfg.model_dump(), "flags": {**cfg.flags, "planning": "off", "verification": "off"}})
    r = A2System(_rt(bi_dev_sqlite, llm), noplan).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r.answer.status == "ANSWERED"  # no crash; the error came back as an observation
    obs = _result_for(llm, "t2")
    assert "TYPE_ERROR" in obs or "TOOL_ERROR" in obs


def test_binary_feedback_hides_validate_and_metadata(bi_dev_sqlite):
    base = load_system("a1")
    binary = SystemConfig(**{**base.model_dump(), "flags": {**base.flags, "execution_feedback": "binary"}})
    llm = FakeLLM([tool_call("validate_sql", {"purpose": "p", "sql": "SELECT nope FROM orders"}, "t1"),
                   tool_call("execute_readonly_sql", {"purpose": "p", "sql": "SELECT status FROM orders", "label": None}, "t2"),
                   tool_call("get_query_result_metadata", {"purpose": "p", "query_id": "q1", "columns": None}, "t3"),
                   tool_call("submit_answer", _answer(), "t4")], model="claude-opus-5-5")
    A1System(_rt(bi_dev_sqlite, llm), binary).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    seen = " ".join(str(b["content"]) for m in llm.calls[-1].messages if m["role"] == "user"
                    and isinstance(m["content"], list) for b in m["content"])
    for leak in ("could not be resolved", "hint", "top_values", "completed", "did you mean"):
        assert leak not in seen, leak


def test_fm1_fault_lands_on_first_agent_query_even_after_exploration(bi_dev_sqlite):
    llm = FakeLLM([tool_call("sample_rows", {"purpose": "p", "table": "orders", "columns": ["status"], "n": 2}, "t1"),
                   tool_call("execute_readonly_sql", {"purpose": "p", "sql": "SELECT 1 AS x", "label": None}, "t2"),
                   tool_call("execute_readonly_sql", {"purpose": "p", "sql": "SELECT 1 AS x", "label": None}, "t3"),
                   tool_call("submit_answer", _answer("q2"), "t4")], model="claude-opus-5-5")
    rt = _rt(bi_dev_sqlite, llm)
    rt.fault_hook = transient_fault_once()
    r = A1System(rt, load_system("a1")).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert [q["status"] for q in r.queries] == ["ERROR", "OK"]
    assert '"status":"OK"' in _result_for(llm, "t1")  # sample_rows succeeded; the fault hit the agent query


def test_a2_tool_results_never_contain_canonical_totals(bi_dev_sqlite):
    from agent_sql.db.executor import Executor, ResultStore
    from agent_sql.systems.a2_planner import A2System
    from agent_sql.verification.metrics_registry import load_registry

    sql = ("SELECT r.region_name, COUNT(*) AS n FROM orders o JOIN regions r ON r.region_id = o.ship_region_id "
           "GROUP BY r.region_name")
    plan = {"steps": [{"step_id": "s1", "goal": "g", "expected_output": "table", "metric_ids": ["net_revenue"],
                       "depends_on": []}], "periods": [{"label": "Q3", "start": "2025-07-01", "end": "2025-10-01"}],
            "assumptions": []}
    llm = FakeLLM([tool_call("submit_plan", plan, "t1"),
                   tool_call("execute_readonly_sql", {"purpose": "p", "sql": sql, "label": None}, "t2"),
                   tool_call("complete_step", {"step_id": "s1", "query_id": "q1", "summary": "s",
                                               "unanswerable_reason": None}, "t3"),
                   tool_call("submit_answer", _answer(), "t4")], model="claude-opus-5-5")
    A2System(_rt(bi_dev_sqlite, llm), load_system("a2")).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    total = Executor(bi_dev_sqlite, statement_timeout_s=30, max_rows=1).run(
        load_registry().get("net_revenue").render_template("2025-07-01", "2025-10-01"), ResultStore()).result.rows[0][0]
    seen = " ".join(str(b["content"]) for m in llm.calls[-1].messages if m["role"] == "user"
                    and isinstance(m["content"], list) for b in m["content"])
    assert f"{total:.2f}" not in seen and str(int(total)) not in seen


def _result_for(llm, tool_use_id):  # noqa: ANN001, ANN202
    for m in llm.calls[-1].messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("tool_use_id") == tool_use_id:
                    return b["content"]
    raise AssertionError(tool_use_id)
