import pytest

from agent_sql.llm.base import LLMRequest, Usage
from agent_sql.llm.cache import CachedLLM, CacheMiss
from agent_sql.llm.fake import FakeLLM, tool_call
from agent_sql.llm.pricing import load_pricing


def _req(text="hi", **meta):
    return LLMRequest("claude-opus-5-5", "sys", [{"role": "user", "content": text}], 100, "medium", metadata=meta)


def test_request_hash_ignores_metadata():
    assert _req(task_id="a").request_hash == _req(task_id="b").request_hash
    assert _req("x").request_hash != _req("y").request_hash


def test_fake_llm_scripts_and_exhaustion():
    llm = FakeLLM(["one", tool_call("list_tables", {"purpose": "p", "name_filter": None})])
    assert llm.complete(_req()).text() == "one"
    assert llm.complete(_req()).tool_uses()[0]["name"] == "list_tables"
    with pytest.raises(AssertionError):
        llm.complete(_req())


def test_record_then_replay(tmp_path):
    path = tmp_path / "cache.jsonl"
    rec = CachedLLM(path, "record", FakeLLM(["answer"], model="claude-opus-5-5"))
    first = rec.complete(_req())
    replay = CachedLLM(path, "replay")
    again = replay.complete(_req())
    assert again.content == first.content and again.usage == first.usage
    with pytest.raises(CacheMiss):
        replay.complete(_req("different"))


def test_pricing_math():
    p = load_pricing()
    u = Usage(input_tokens=1_000_000, output_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    assert p.cost("claude-opus-5-5", u) == pytest.approx(4.0 + 20.0 + 0.20)
    assert p.uncached_cost("claude-opus-5-5", u) == pytest.approx(8.0 + 20.0)
    with pytest.raises(KeyError):
        p.cost("unknown-model", u)
