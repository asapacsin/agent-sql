"""Streamlit AppTest smoke tests (Phase 5 gate): pages render answer, tables, SQL, history, trace, validation,
warnings and comparison metrics using FakeLLM; no private reasoning is displayed."""
from __future__ import annotations

import json

import pytest
from streamlit.testing.v1 import AppTest

from agent_sql.db.registry import bi_sqlite_path
from agent_sql.llm.fake import FakeLLM
from agent_sql.ui.charts import choose_chart
from agent_sql.ui.service import public_trace
from tests.fake_policy import policy

APP = str(__import__("pathlib").Path(__file__).resolve().parents[2] / "agent_sql" / "ui" / "app.py")
pytestmark = pytest.mark.skipif(not bi_sqlite_path("dev").exists(), reason="RetailCo-dev not generated")


def thinking_policy(req, i):  # noqa: ANN001, ANN201
    out = policy(req, i)
    if isinstance(out, dict):
        out = {**out, "content": [{"type": "thinking", "thinking": "SECRET-REASONING"}, *out["content"]]}
    return out


def app(page: str) -> AppTest:
    at = AppTest.from_file(APP, default_timeout=120)
    at.session_state["llm_override"] = FakeLLM(thinking_policy, model="claude-opus-5-5")
    at.run()
    at.sidebar.radio(key="page").set_value(page).run()
    return at


def all_text(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [str(c.value) for c in at.code] + [str(j.value) for j in at.json]
    parts += [w.value for w in at.warning] + [s.value for s in at.subheader] + [c.value for c in at.caption]
    return "\n".join(map(str, parts))


def test_ask_page_renders_everything():
    at = app("Ask")
    at.selectbox(key="ask-system").set_value("a2")
    at.text_input(key="ask-q").set_value("How many orders are there by status?")
    at.button(key="ask-run").click().run()
    assert not at.exception, at.exception
    text = all_text(at)
    assert "ANSWERED" in text
    heads = [s.value for s in at.subheader]
    for h in ("Generated SQL", "Execution history", "Validation outcomes"):
        assert h in heads, heads
    assert any(h.startswith("Result q") for h in heads)
    assert "SELECT" in "\n".join(str(c.value) for c in at.code)
    assert "SECRET-REASONING" not in text, "private reasoning must never be displayed"


def test_compare_page_shows_both_systems_and_metrics():
    at = app("Compare")
    at.text_input(key="cmp-q").set_value("How many orders are there by status?")
    at.button(key="cmp-run").click().run()
    assert not at.exception, at.exception
    heads = [s.value for s in at.subheader]
    assert "B0" in heads and "A2" in heads
    metrics = next(d.value for d in at.dataframe if "latency_s" in list(d.value.index))
    for row in ("status", "latency_s", "input_tokens", "output_tokens", "tool_calls", "est_cost_usd"):
        assert row in metrics.index


def test_trace_page_renders_public_trace_only():
    at = app("Trace")
    events = [{"seq": 0, "type": "llm_call", "content": [{"type": "text", "text": "x"}]},
              {"seq": 1, "type": "tool_call", "tool": "list_tables", "purpose": "look", "args": {}},
              {"seq": 2, "type": "tool_result", "tool": "list_tables", "status": "OK"}]
    at.text_area(key="trace-text").set_value("\n".join(json.dumps(e) for e in events)).run()
    shown = json.dumps(at.json[0].value)
    assert "list_tables" in shown and "llm_call" not in shown


def test_no_credential_message(monkeypatch):
    import agent_sql.ui.service as svc

    anthropic_cfg = svc.load_model_settings().model_copy(update={"provider": "anthropic"})
    monkeypatch.setattr(svc, "load_model_settings", lambda: anthropic_cfg)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    at = AppTest.from_file(APP, default_timeout=60)
    at.run()
    at.text_input(key="ask-q").set_value("q")
    at.button(key="ask-run").click().run()
    assert any("No Anthropic credential" in e.value for e in at.error)


def test_chart_rules():
    assert choose_chart(["month", "revenue"], [(1, 1.0), (2, 2.0), (3, 3.0)]).kind == "line"
    assert choose_chart(["region", "revenue"], [("W", 1.0), ("E", 2.0)]).kind == "bar"
    assert choose_chart(["a", "b", "c"], [("x", "y", "z")]).kind == "table"
    assert choose_chart(["region", "revenue"], [("W", 1.0), ("E", 2.0)], {"kind": "line", "x": "region",
                                                                          "y": "revenue"}).kind == "bar"


def test_public_trace_excludes_llm_content():
    ev = [{"seq": 0, "type": "llm_call", "content": [{"type": "text", "text": "secret"}]},
          {"seq": 1, "type": "state", "frm": "A", "to": "B", "reason": "r", "run_id": "x", "task_id": "t",
           "system_id": "s", "t": 0}]
    out = public_trace(ev)
    assert len(out) == 1 and "secret" not in json.dumps(out)
