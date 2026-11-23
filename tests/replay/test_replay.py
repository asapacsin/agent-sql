"""Record/replay reproduces predictions exactly (excluding wall-clock fields and run_id).

This proves the replay mechanism with a FakeLLM cassette. Replay of a LIVE cassette (p1-live-smoke) is a
separate, credential-dependent piece of evidence and is NOT claimed by this test.
"""
from __future__ import annotations

import json

import pytest

from agent_sql.eval.runner import DatasetRef, ExperimentConfig, Runner
from agent_sql.llm.cache import CacheMiss
from agent_sql.llm.fake import FakeLLM

VOLATILE = {"latency_s", "llm_latency_s", "db_latency_s", "elapsed_ms", "run_id", "trace_path", "t"}


def strip(obj):
    if isinstance(obj, dict):
        return {k: strip(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [strip(v) for v in obj]
    return obj


def script(req, i):
    if req.output_schema and "sql" in req.output_schema["properties"]:
        return json.dumps({"sql": "SELECT status, COUNT(*) AS n FROM orders GROUP BY status"})
    return json.dumps({"answer_text": "a", "claims": [], "metric_interpretations": [], "assumptions": [],
                       "limitations": [], "uncertainty": {"level": "medium", "reasons": []}, "chart": None})


def _preds(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    return sorted((strip(r) for r in rows), key=lambda r: (r["run_index"], r["task_id"], r["system_id"]))


def test_replay_reproduces_predictions(tmp_path, bi_dev_sqlite):
    cache = tmp_path / "cache.jsonl"
    cfg = ExperimentConfig(experiment_id="replay", datasets=[DatasetRef(dataset="bi", split="dev", limit=5)],
                           systems=["b0"], runs=1, concurrency=2, llm={"mode": "record", "cache": str(cache)})
    Runner(cfg, llm=FakeLLM(script, model="claude-opus-5-5"), allow_dirty=True, out_dir=tmp_path / "rec",
           databases={"retailco_dev": bi_dev_sqlite}).run()
    Runner(cfg, replay=True, allow_dirty=True, out_dir=tmp_path / "rep",
           databases={"retailco_dev": bi_dev_sqlite}).run()
    assert _preds(tmp_path / "rec" / "predictions.jsonl") == _preds(tmp_path / "rep" / "predictions.jsonl")


def test_replay_miss_fails(tmp_path, bi_dev_sqlite):
    from agent_sql.llm.base import LLMRequest
    from agent_sql.llm.cache import CachedLLM

    with pytest.raises(CacheMiss):
        CachedLLM(tmp_path / "empty.jsonl", "replay").complete(
            LLMRequest("claude-opus-5-5", "s", [{"role": "user", "content": "x"}], 10, "medium"))
