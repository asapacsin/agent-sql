"""Live smoke tests (marker `live`): real provider calls (provider from configs/models.yaml). Never in default CI."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent_sql.config.settings import load_model_settings

pytestmark = pytest.mark.live
MODEL = load_model_settings()
# openai_compatible: the key comes from DASHSCOPE_API_KEY, or from a network secret the egress proxy injects; in the
# latter case set AGENT_SQL_PROXY_CREDENTIAL=1 (or DASHSCOPE_API_KEY=proxy-injected) so a missing key reports BLOCKED.
HAS_CREDENTIAL = (
    bool(os.environ.get("DASHSCOPE_API_KEY") or os.environ.get("AGENT_SQL_PROXY_CREDENTIAL"))
    if MODEL.provider == "openai_compatible"
    else bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")))


@pytest.mark.skipif(not HAS_CREDENTIAL, reason="BLOCKED_EXTERNAL: no provider credential in the environment")
def test_live_b0_bi_dev(tmp_path):
    from agent_sql.eval.cli import score_run
    from agent_sql.eval.report import load_scores
    from agent_sql.eval.runner import DatasetRef, ExperimentConfig, Runner

    cfg = ExperimentConfig(experiment_id="p1_live_smoke", datasets=[DatasetRef(dataset="bi", split="dev")],
                           systems=["b0"], runs=1, concurrency=2, max_experiment_usd=5.0, exploratory=True)
    out = Runner(cfg, allow_dirty=True, out_dir=tmp_path).run()
    assert out.completed == 20 and not out.errors, out.errors
    score_run(tmp_path)
    scores = load_scores(tmp_path / "scores.jsonl")
    assert len(scores) == 20
    assert not any(s["status"] == "FAILED_PROVIDER" for s in scores), [s["detail"] for s in scores]
    # every task leaves a non-empty trace
    import json

    from agent_sql.eval.report import summarize
    from agent_sql.eval.runner import read_predictions
    preds = [p for _, p in read_predictions(tmp_path)]
    assert len(preds) == 20
    for p in preds:
        trace = tmp_path / "traces" / Path(p.trace_path).name
        assert trace.exists() and trace.stat().st_size > 0, p.task_id
        lines = [json.loads(x) for x in trace.read_text().splitlines()]
        assert lines, p.task_id
        # never fabricate: an answer that is not ANSWERED carries no numeric claims
        ans = p.answer
        if ans.status != "ANSWERED":
            assert not [c for c in ans.claims if c.value is not None], (p.task_id, ans.status)
    # the metric summary (M01-M12 inputs) is computable from the scores
    assert summarize(scores)
