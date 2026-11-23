"""Live smoke tests (marker `live`): real provider calls. Skipped without credentials; never run in default CI."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.live
HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


@pytest.mark.skipif(not HAS_KEY, reason="BLOCKED_EXTERNAL: no Anthropic credential in the environment")
def test_live_b0_bi_dev(tmp_path):
    from agent_sql.eval.cli import score_run
    from agent_sql.eval.runner import DatasetRef, ExperimentConfig, Runner

    cfg = ExperimentConfig(experiment_id="p1_live_smoke", datasets=[DatasetRef(dataset="bi", split="dev")],
                           systems=["b0"], runs=1, concurrency=2, max_experiment_usd=5.0)
    out = Runner(cfg, allow_dirty=True, out_dir=tmp_path).run()
    assert out.completed == 20 and not out.errors
    score_run(tmp_path)
