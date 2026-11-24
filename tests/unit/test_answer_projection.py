"""ANSWER_PROJECTION (CHANGELOG A23): the model picks answer_columns; deterministic code builds the scored query."""
from __future__ import annotations

import sqlite3

from agent_sql.orchestration.agent_loop import answer_column_problems, projection_sql


def test_projection_keeps_rows_and_order_and_selects_only_chosen_columns():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE p (name TEXT, rating INT, d TEXT)")
    conn.executemany("INSERT INTO p VALUES (?, ?, ?)", [("a", 3, "x"), ("b", 9, "y"), ("c", 5, "z")])
    src = "SELECT name, rating AS \"overall rating\", d FROM p ORDER BY rating DESC LIMIT 2;"
    rows = conn.execute(projection_sql(src, ["name"])).fetchall()
    assert rows == [("b",), ("c",)]
    rows = conn.execute(projection_sql(src, ["overall rating", "name"])).fetchall()
    assert rows == [(9, "b"), (5, "c")]


def test_answer_columns_contract():
    cols = ["name", "n", "n"]
    assert answer_column_problems(None, cols, True)            # required with ANSWERED
    assert not answer_column_problems(None, cols, False)       # budget FINALIZE path is lenient
    assert answer_column_problems(["missing"], cols, True)      # unknown column
    assert answer_column_problems(["n"], cols, True)            # ambiguous duplicate name
    assert answer_column_problems(["name", "name"], cols, True)  # repeated choice
    assert not answer_column_problems(["name"], cols, True)


def test_json_string_array_is_coerced_and_logged():
    """Val v3 incident: Qwen sent answer_columns as the string '["a", "b"]'."""
    from agent_sql.tools.registry import coerce_args, validate_args
    from agent_sql.tools.sql_tools import SUBMIT_ANSWER

    args = {"status": "ANSWERED", "answer_text": "x", "final_query_id": "q1",
            "answer_columns": '["department", "net_revenue"]', "claims": [], "charts": [],
            "metric_interpretations": [], "assumptions": [], "limitations": [],
            "uncertainty": {"level": "low", "reasons": []}}
    fixed, paths = coerce_args(SUBMIT_ANSWER.schema, args)
    assert fixed["answer_columns"] == ["department", "net_revenue"] and paths == ["$.answer_columns"]
    validate_args(SUBMIT_ANSWER.schema, fixed)
    # a string that is not a JSON array is left alone and still rejected by strict validation
    bad, paths = coerce_args(SUBMIT_ANSWER.schema, {**args, "answer_columns": "department"})
    assert paths == [] and bad["answer_columns"] == "department"
    # string-typed fields are never parsed
    s, paths = coerce_args(SUBMIT_ANSWER.schema, {**fixed, "answer_text": "[1, 2]"})
    assert s["answer_text"] == "[1, 2]" and paths == []


def test_runner_refuses_a_second_concurrent_runner(tmp_path):
    import os

    import pytest

    from agent_sql.eval.runner import DatasetRef, ExperimentConfig, Runner

    cfg = ExperimentConfig(experiment_id="lock", datasets=[DatasetRef(dataset="bi", split="dev", limit=1)],
                           systems=["b0"], runs=1, llm={"mode": "replay", "cache": str(tmp_path / "c.jsonl")})
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / ".runner.lock").write_text(str(os.getpid()))  # a live process holds the lock
    with pytest.raises(SystemExit, match="another runner"):
        Runner(cfg, replay=True, allow_dirty=True, out_dir=tmp_path / "out").run()
