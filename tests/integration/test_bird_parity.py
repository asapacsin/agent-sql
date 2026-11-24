"""Scorer parity with the official BIRD evaluation `execute_sql` (Phase 2 gate; strengthened after review).

Runs through the PRODUCTION scoring path (Scorer, validator on, eval limits) over all 350 frozen split tasks with
adversarial variants: identity, reordering (ORDER BY 1 DESC / 2), duplicated rows (UNION ALL), subset (LIMIT 1),
empty, DISTINCT, constant projection, erroring SQL. Large-result tasks exercise the uncapped comparison.
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


class _OfficialTimeout(Exception):
    pass


def _official_with_timeout(pred: str, gold: str, path: str, seconds: float) -> int:
    """The official script wraps execute_sql in func_timeout(meta_time_out) and scores a timeout or error as 0.
    We apply the same rule with the evaluator's own timeout (eval_reexec_timeout_s) so both sides time out alike."""
    import signal

    def handler(signum, frame):  # noqa: ANN001, ARG001
        raise _OfficialTimeout()

    old = signal.signal(signal.SIGALRM, handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return official.execute_sql(pred, gold, path)
    except Exception:  # noqa: BLE001 - official script scores errors and timeouts as 0
        return 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def _check_task(task_id: str) -> tuple[int, list]:
    """Worker: compare our Scorer with the official execute_sql on every variant of one task."""
    ev = load_eval_settings()
    t = next(x for split in ("dev", "val", "test") for x in bird.load_bird_tasks(split) if x.task_id == task_id)
    db = t.db_id.removeprefix("bird:")
    scorer = Scorer({t.db_id: bird.open_bird_db(db)}, ev.eval_reexec_timeout_s, ev.eval_reexec_max_rows)
    path = str(bird.db_path(db))
    n, bad = 0, []
    for v in variants(t.gold["sql"], 2):
        theirs = _official_with_timeout(v, t.gold["sql"], path, ev.eval_reexec_timeout_s)
        pred = RunResult(task_id=t.task_id, system_id="parity", run_id="p",
                         answer=FinalAnswer(status="ANSWERED", final_query_id="q1"),
                         queries=[{"query_id": "q1", "sql": v, "status": "OK", "error_class": None}])
        ours = int(scorer.score(t, pred).correct)
        n += 1
        if ours != theirs:
            bad.append((t.task_id, v[:80], ours, theirs))
    return n, bad


def _code_fingerprint() -> str:
    """Checkpoints are valid only for the exact scorer/comparator/validator/executor code and these variants."""
    import hashlib

    root = Path(__file__).resolve().parents[2]
    files = ["agent_sql/eval/scoring.py", "agent_sql/eval/comparators.py", "agent_sql/safety/validator.py",
             "agent_sql/db/executor.py", "agent_sql/verification/numeric.py", "tests/integration/test_bird_parity.py",
             "data/splits/bird.json"]
    h = hashlib.sha256()
    for f in files:
        h.update((root / f).read_bytes())
    return h.hexdigest()[:16]


CHECKPOINT = Path(__file__).resolve().parents[2] / ".ai-native" / "parity_checkpoint.jsonl"


@pytest.mark.skipif(not bird.DEV_JSON.exists(), reason="BIRD not downloaded")
def test_parity_with_official_execute_sql_through_scorer():
    """Resumable: per-task results are checkpointed (keyed by a code fingerprint) so container restarts do not lose
    completed work; a checkpoint from different code is ignored."""
    import json
    from concurrent.futures import ProcessPoolExecutor

    fp = _code_fingerprint()
    ids = [t.task_id for split in ("dev", "val", "test") for t in bird.load_bird_tasks(split)]
    assert len(ids) == 350
    done: dict[str, tuple[int, list]] = {}
    if CHECKPOINT.exists():
        for line in CHECKPOINT.read_text().splitlines():
            r = json.loads(line)
            if r["fp"] == fp:
                done[r["task_id"]] = (r["n"], r["bad"])
    todo = [i for i in ids if i not in done]
    CHECKPOINT.parent.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max_workers=4) as pool, open(CHECKPOINT, "a", encoding="utf-8") as ck:
        for task_id, (k, b) in zip(todo, pool.map(_check_task, todo, chunksize=1), strict=True):
            done[task_id] = (k, b)
            ck.write(json.dumps({"fp": fp, "task_id": task_id, "n": k, "bad": b}) + "\n")
            ck.flush()
    n = sum(v[0] for v in done.values())
    bad = [x for v in done.values() for x in v[1]]
    assert n >= 2800
    assert not bad, f"{len(bad)}/{n} disagreements, e.g. {bad[:5]}"
