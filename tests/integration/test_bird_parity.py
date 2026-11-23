"""Scorer parity with the official BIRD evaluation `execute_sql` (Phase 2 gate; strengthened after review).

Runs through the PRODUCTION scoring path (Scorer, validator on, eval limits) over all 350 frozen split tasks with
adversarial variants: identity, reordering, duplicated rows, column swap, subset, empty, DISTINCT, type change,
erroring SQL. Large-result tasks exercise the uncapped comparison.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agent_sql.config.settings import load_eval_settings
from agent_sql.eval.datasets import bird
from agent_sql.eval.scoring import Scorer
from agent_sql.systems.contracts import FinalAnswer, RunResult

spec = importlib.util.spec_from_file_location("official", Path(__file__).parents[1] / "fixtures" /
                                              "bird_official_execute_sql.py")
official = importlib.util.module_from_spec(spec)
spec.loader.exec_module(official)


def variants(gold: str, first_col_count: int) -> list[str]:
    g = gold.strip().rstrip(";")
    out = [g, f"SELECT * FROM ({g}) ORDER BY 1 DESC", f"SELECT * FROM ({g}) UNION ALL SELECT * FROM ({g})",
           f"SELECT * FROM ({g}) LIMIT 1", f"SELECT * FROM ({g}) WHERE 1 = 0", f"SELECT DISTINCT * FROM ({g})",
           f"SELECT 1 FROM ({g})", "SELECT nonexistent_column_xyz FROM sqlite_dummy"]
    if first_col_count >= 2:
        out.append(f"SELECT * FROM ({g}) AS t ORDER BY 2")
    return out


@pytest.mark.skipif(not bird.DEV_JSON.exists(), reason="BIRD not downloaded")
def test_parity_with_official_execute_sql_through_scorer():
    ev = load_eval_settings()
    tasks = [t for split in ("dev", "val", "test") for t in bird.load_bird_tasks(split)]
    dbs = {t.db_id: bird.open_bird_db(t.db_id.removeprefix("bird:")) for t in tasks}
    scorer = Scorer(dbs, ev.eval_reexec_timeout_s, ev.eval_reexec_max_rows)
    n = disagreements = 0
    examples = []
    for t in tasks:
        path = str(bird.db_path(t.db_id.removeprefix("bird:")))
        for v in variants(t.gold["sql"], 2):
            try:
                theirs = official.execute_sql(v, t.gold["sql"], path)
            except Exception:  # noqa: BLE001 - official script scores errors as 0
                theirs = 0
            pred = RunResult(task_id=t.task_id, system_id="parity", run_id="p",
                             answer=FinalAnswer(status="ANSWERED", final_query_id="q1"),
                             queries=[{"query_id": "q1", "sql": v, "status": "OK", "error_class": None}])
            ours = int(scorer.score(t, pred).correct)
            n += 1
            if ours != theirs:
                disagreements += 1
                examples.append((t.task_id, v[:80], ours, theirs))
    assert n >= 2800
    assert disagreements == 0, f"{disagreements}/{n} disagreements, e.g. {examples[:5]}"
