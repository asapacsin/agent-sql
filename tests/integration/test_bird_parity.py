"""Comparator parity with the official BIRD evaluation `execute_sql` on >= 300 predictions (Phase 2 gate)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agent_sql.db.executor import Executor, ResultStore
from agent_sql.eval.comparators import bird_execution_match
from agent_sql.eval.datasets import bird

spec = importlib.util.spec_from_file_location("official", Path(__file__).parents[1] / "fixtures" /
                                              "bird_official_execute_sql.py")
official = importlib.util.module_from_spec(spec)
spec.loader.exec_module(official)


def variants(gold: str) -> list[str]:
    g = gold.strip().rstrip(";")
    return [g, f"SELECT * FROM ({g}) LIMIT 1", f"SELECT * FROM ({g}) WHERE 1 = 0", f"SELECT DISTINCT * FROM ({g})"]


@pytest.mark.skipif(not bird.DEV_JSON.exists(), reason="BIRD not downloaded")
def test_parity_with_official_execute_sql():
    tasks = bird.load_bird_tasks("dev")
    n = disagreements = 0
    for t in tasks:
        db = t.db_id.removeprefix("bird:")
        path = str(bird.db_path(db))
        ex = Executor(bird.open_bird_db(db), statement_timeout_s=60, max_rows=100_000, validate=False)
        gold = ex.run(t.gold["sql"], ResultStore())
        assert gold.ok, (t.task_id, gold.message)
        for pred in variants(t.gold["sql"]):
            try:
                theirs = official.execute_sql(pred, t.gold["sql"], path)
            except Exception:  # noqa: BLE001 - official script scores errors as 0
                theirs = 0
            rec = ex.run(pred, ResultStore())
            ours = int(rec.ok and bird_execution_match(rec.result.rows, gold.result.rows))
            n += 1
            if ours != theirs:
                disagreements += 1
    assert n >= 300
    assert disagreements == 0, f"{disagreements}/{n} disagreements with the official evaluator"
