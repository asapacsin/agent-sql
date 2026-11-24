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
