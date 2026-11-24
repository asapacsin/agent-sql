"""Property test: random scripted agents ALWAYS terminate within budgets and never answer without evidence."""
from __future__ import annotations

import random

import pytest

from agent_sql.config.settings import load_limits, load_model_settings, load_system
from agent_sql.db.registry import bi_sqlite_path, open_bi
from agent_sql.llm.base import ProviderError
from agent_sql.llm.fake import FakeLLM, text_response
from agent_sql.llm.pricing import load_pricing
from agent_sql.systems.a1_agent import A1System
from agent_sql.systems.base import Runtime
from agent_sql.systems.contracts import TaskInput

N_SEQUENCES = 1000
GOOD = ["SELECT COUNT(*) AS n FROM orders", "SELECT status, COUNT(*) AS n FROM orders GROUP BY status",
        "SELECT region_name FROM regions"]
BAD = ["SELECT nope FROM orders", "DELETE FROM orders", "SELECT 1; SELECT 2", "SELECT * FROM customers",
       "SELEC x"]


def random_action(rng: random.Random, i: int) -> dict:
    uid = f"toolu_{i}"
    r = rng.random()
    if r < 0.05:
        return text_response("thinking out loud without a tool")
    if r < 0.08:
        raise ProviderError("simulated outage")
    calls = []
    for k in range(rng.choice([1, 1, 1, 2, 3])):
        x = rng.random()
        cid = f"{uid}_{k}"
        if x < 0.30:
            calls.append(("execute_readonly_sql", {"purpose": "p", "sql": rng.choice(GOOD + BAD), "label": None}, cid))
        elif x < 0.40:
            calls.append(("validate_sql", {"purpose": "p", "sql": rng.choice(GOOD + BAD)}, cid))
        elif x < 0.50:
            calls.append(("describe_table", {"purpose": "p", "table": rng.choice(["orders", "nope", "customers"])}, cid))
        elif x < 0.55:
            calls.append(("list_tables", {"purpose": "p", "name_filter": None}, cid))
        elif x < 0.60:
            calls.append(("not_a_tool", {"purpose": "p"}, cid))
        elif x < 0.65:
            calls.append(("execute_readonly_sql", {"sql": 7}, cid))  # invalid args
        elif x < 0.72:
            calls.append(("get_query_result_metadata", {"purpose": "p", "query_id": f"q{rng.randint(1, 4)}",
                                                        "columns": None}, cid))
        else:
            qid = f"q{rng.randint(1, 5)}"
            status = rng.choice(["ANSWERED", "ANSWERED", "INSUFFICIENT_DATA"])
            claims = [] if rng.random() < 0.3 else [{"text": "n", "value": rng.choice([5, 60418, "x"]), "unit": None,
                                                     "entities": [], "query_id": rng.choice([qid, None]),
                                                     "row_index": rng.choice([0, 3, None]), "column": "n"}]
            calls.append(("submit_answer", {"status": status, "answer_text": "a", "final_query_id": qid,
                                            "answer_columns": rng.choice([["n"], None, ["missing"]]),
                                            "claims": claims, "charts": [], "metric_interpretations": [],
                                            "assumptions": [], "limitations": [],
                                            "uncertainty": {"level": "low", "reasons": []}}, cid))
    return {"content": [{"type": "tool_use", "id": c, "name": n, "input": a} for n, a, c in calls],
            "stop_reason": "tool_use"}


@pytest.fixture(scope="module")
def db():
    if not bi_sqlite_path("dev").exists():
        pytest.skip("RetailCo-dev not generated")
    return open_bi("dev")


def test_random_agents_terminate_within_budgets(db):
    cfg = load_system("a1")
    b = cfg.budgets
    rng = random.Random(20261123)
    statuses = {}
    for seq in range(N_SEQUENCES):
        local = random.Random(rng.random())
        llm = FakeLLM(lambda req, i, local=local: random_action(local, i), model="claude-opus-5-5")
        rt = Runtime(db, llm, load_model_settings(), load_limits(), load_pricing())
        r = A1System(rt, cfg).run(TaskInput(task_id=f"p{seq}", db_id="retailco_dev", question="How many orders?"))
        statuses[r.answer.status] = statuses.get(r.answer.status, 0) + 1
        assert r.counters.llm_calls <= b.max_llm_calls + 1, "at most one FINALIZE call beyond the LLM budget"
        assert r.counters.tool_calls <= b.max_tool_calls + 1  # the call that trips the budget is counted, then refused
        sql_failures = sum(1 for q in r.queries if q["status"] != "OK")
        assert sql_failures <= b.max_repair_attempts + 1  # the 4th failure terminates
        if r.answer.status == "ANSWERED":
            final = next((q for q in r.queries if q["query_id"] == r.answer.final_query_id), None)
            assert final is not None and final["status"] == "OK"
            for c in r.answer.claims:
                if c.is_numeric:
                    assert c.evidence is not None
        else:
            assert not any(c.is_numeric for c in r.answer.claims)
    assert sum(statuses.values()) == N_SEQUENCES
    assert len(statuses) >= 4, f"property test should exercise several terminal statuses: {statuses}"


def test_fourth_failure_terminates(db):
    bad = [{"content": [{"type": "tool_use", "id": f"t{i}", "name": "execute_readonly_sql",
                         "input": {"purpose": "p", "sql": "SELECT nope FROM orders", "label": None}}],
            "stop_reason": "tool_use"} for i in range(10)]
    llm = FakeLLM(bad, model="claude-opus-5-5")
    rt = Runtime(db, llm, load_model_settings(), load_limits(), load_pricing())
    r = A1System(rt, load_system("a1")).run(TaskInput(task_id="t", db_id="retailco_dev", question="q"))
    assert r.answer.status == "FAILED_REPAIR_BUDGET" and len(llm.calls) == 4 and len(r.queries) == 4
