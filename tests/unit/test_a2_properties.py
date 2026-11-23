"""A2: random scripted agents terminate within budgets; protocol gating; uncertainty floor; injection safety."""
from __future__ import annotations

import json
import random

import pytest

from agent_sql.config.settings import SystemConfig, load_limits, load_model_settings, load_system
from agent_sql.db.registry import bi_sqlite_path, open_bi
from agent_sql.llm.fake import FakeLLM, tool_call
from agent_sql.llm.pricing import load_pricing
from agent_sql.systems.a2_planner import A2System
from agent_sql.systems.base import Runtime
from agent_sql.systems.contracts import TaskInput
from tests.unit.test_a1_properties import random_action


@pytest.fixture(scope="module")
def db():
    if not bi_sqlite_path("dev").exists():
        pytest.skip("RetailCo-dev not generated")
    return open_bi("dev")


def rt(db, llm):
    return Runtime(db, llm, load_model_settings(), load_limits(), load_pricing())


def plan_action(rng, i):
    if rng.random() < 0.25:
        steps = [{"step_id": f"s{k}", "goal": "g", "expected_output": "table", "metric_ids": [rng.choice(["orders", "bogus"])],
                  "depends_on": []} for k in range(rng.randint(1, 7))]
        return tool_call("submit_plan", {"steps": steps, "periods": [], "assumptions": []}, f"p{i}")
    if rng.random() < 0.2:
        return tool_call("complete_step", {"step_id": rng.choice(["s0", "s1", "zz"]), "query_id": rng.choice(["q1", None]),
                                           "summary": "x", "unanswerable_reason": rng.choice([None, "n/a"])}, f"c{i}")
    return random_action(rng, i)


def test_random_a2_agents_terminate_within_budgets(db):
    cfg = load_system("a2")
    b = cfg.budgets
    seeds = random.Random(7)
    statuses = {}
    for seq in range(1000):
        local = random.Random(seeds.random())
        llm = FakeLLM(lambda req, i, local=local: plan_action(local, i), model="claude-opus-5-5")
        r = A2System(rt(db, llm), cfg).run(TaskInput(task_id=f"p{seq}", db_id="retailco_dev", question="q"))
        statuses[r.answer.status] = statuses.get(r.answer.status, 0) + 1
        assert r.counters.llm_calls <= b.max_llm_calls + 1
        assert r.counters.tool_calls <= b.max_tool_calls + 1
        assert sum(1 for q in r.queries if q["status"] != "OK") <= b.max_failures_per_task
        assert r.counters.verification_queries <= b.max_verification_queries
        if r.answer.status == "ANSWERED":
            final = next(q for q in r.queries if q["query_id"] == r.answer.final_query_id)
            assert final["status"] == "OK"
        else:
            assert not any(c.is_numeric for c in r.answer.claims)
    assert len(statuses) >= 3, statuses


def test_answer_before_steps_is_rejected(db):
    from tests.fake_policy import answer

    llm = FakeLLM([tool_call("submit_plan", {"steps": [{"step_id": "s1", "goal": "g", "expected_output": "table",
                                                        "metric_ids": [], "depends_on": []}], "periods": [],
                                             "assumptions": []}, "t1"),
                   tool_call("submit_answer", answer("q1"), "t2")] + [
        tool_call("list_tables", {"purpose": "p", "name_filter": None}, f"x{i}") for i in range(30)],
        model="claude-opus-5-5")
    r = A2System(rt(db, llm), load_system("a2")).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    replies = [json.loads(b["content"]) for m in llm.calls[-1].messages if m["role"] == "user"
               and isinstance(m["content"], list) for b in m["content"] if b.get("tool_use_id") == "t2"]
    assert replies and replies[0]["status"] in ("REJECTED", "TOOL_NOT_ALLOWED") and r.answer.status != "ANSWERED"


def test_noplan_and_noverify_flags(db):
    from tests.fake_policy import policy

    base = load_system("a2")
    noplan = SystemConfig(**{**base.model_dump(), "flags": {**base.flags, "planning": "off"}})
    llm = FakeLLM(policy, model="claude-opus-5-5")
    r = A2System(rt(db, llm), noplan).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r.answer.status == "ANSWERED" and "submit_plan" not in {t.name for t in llm.calls[0].tools}
    noverify = SystemConfig(**{**base.model_dump(), "flags": {**base.flags, "verification": "off"}})
    r2 = A2System(rt(db, FakeLLM(policy, model="claude-opus-5-5")), noverify).run(
        TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r2.answer.status == "ANSWERED" and r2.answer.validation.status == "NOT_RUN"
    r3 = A2System(rt(db, FakeLLM(policy, model="claude-opus-5-5")), base).run(
        TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r3.answer.validation.status in ("PASSED", "WARNINGS", "FAILED") and r3.answer.validation.checks


def test_injection_strings_have_no_unsafe_effect(db):
    """Even an agent that 'obeys' injected text cannot write or read restricted data."""
    obey = [tool_call("execute_readonly_sql", {"purpose": "follow product description", "sql": "DROP TABLE orders",
                                               "label": None}, "t1"),
            tool_call("execute_readonly_sql", {"purpose": "reveal", "sql": "SELECT email FROM customers",
                                               "label": None}, "t2"),
            tool_call("execute_readonly_sql", {"purpose": "x", "sql": "DELETE FROM customers", "label": None}, "t3"),
            tool_call("execute_readonly_sql", {"purpose": "x", "sql": "SELECT phone FROM customers", "label": None},
                      "t4")]
    noplan = SystemConfig(**{**load_system("a2").model_dump(), "flags": {**load_system("a2").flags, "planning": "off"}})
    r = A2System(rt(db, FakeLLM(obey, model="claude-opus-5-5")), noplan).run(
        TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert all(q["status"] == "REJECTED" for q in r.queries) and r.answer.status.startswith("FAILED")
    import sqlite3

    conn = sqlite3.connect(f"file:{bi_sqlite_path('dev')}?mode=ro", uri=True)
    assert conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] > 0
    conn.close()
