"""Live smoke tests (marker `live`): real provider calls (provider from configs/models.yaml). Never in default CI."""
from __future__ import annotations

import os

import pytest

from agent_sql.config.settings import load_model_settings

pytestmark = pytest.mark.live
MODEL = load_model_settings()
HAS_CREDENTIAL = MODEL.provider == "openai_compatible" or bool(
    os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


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
