"""Minimal Streamlit BI assistant (docs/spec/PRD.md section 6): Ask, Compare, Trace.

    streamlit run agent_sql/ui/app.py
Renders RunResult only; all logic lives in agent_sql.ui.service and the systems.
"""
from __future__ import annotations

import json
import random

import altair as alt
import pandas as pd
import streamlit as st

from agent_sql.ui.charts import choose_chart
from agent_sql.ui.service import UI_DATABASES, UI_SYSTEMS, UIRun, default_llm, public_trace, run_question

STATUS_COLOR = {"ANSWERED": "green", "INSUFFICIENT_DATA": "orange"}


def get_llm():  # noqa: ANN201
    return st.session_state.get("llm_override") or default_llm()


def render_run(run: UIRun, key: str) -> None:
    r, a = run.result, run.result.answer
    color = STATUS_COLOR.get(a.status, "red")
    st.markdown(f":{color}[**{a.status}**] · validation **{a.validation.status}** · uncertainty **{a.uncertainty.level}**")
    st.write(a.answer_text or "(no answer text)")
    numeric = [c for c in a.claims if c.is_numeric]
    if numeric:
        st.subheader("Key figures")
        st.dataframe(pd.DataFrame([{"claim": c.text, "value": c.value, "unit": c.unit,
                                    "evidence": f"{c.evidence.query_id}[{c.evidence.row_index}].{c.evidence.column}"
                                    if c.evidence else "UNGROUNDED"} for c in numeric]), key=f"{key}-claims")
    for qid, (cols, rows, truncated) in run.tables.items():
        st.subheader(f"Result {qid}" + (" (truncated)" if truncated else ""))
        df = pd.DataFrame(rows, columns=cols)
        st.dataframe(df, key=f"{key}-{qid}")
        hint = next((ch.model_dump() for ch in a.charts if ch.query_id == qid), None)
        chart = choose_chart(cols, rows, hint)
        if chart.kind == "line":
            st.altair_chart(alt.Chart(df).mark_line(point=True).encode(x=chart.x, y=chart.y), key=f"{key}-{qid}-c")
        elif chart.kind == "bar":
            st.altair_chart(alt.Chart(df).mark_bar().encode(x=alt.X(chart.x, sort="-y"), y=chart.y),
                            key=f"{key}-{qid}-c")
    st.subheader("Generated SQL")
    final = next((q for q in r.queries if q["query_id"] == a.final_query_id), None)
    if final:
        st.code(final["sql"], language="sql")
    st.subheader("Execution history")
    st.dataframe(pd.DataFrame([{k: q[k] for k in ("query_id", "status", "error_class", "row_count", "truncated",
                                                   "elapsed_ms")} for q in r.queries]) if r.queries
                 else pd.DataFrame(), key=f"{key}-hist")
    with st.expander("Action trace (tool actions, observations, controller decisions)"):
        st.json(public_trace(run.trace_events))
    st.subheader("Validation outcomes")
    if a.validation.checks:
        st.dataframe(pd.DataFrame([c.model_dump() for c in a.validation.checks]), key=f"{key}-val")
    else:
        st.caption("In-loop validation not run for this system (post-hoc checks run in the benchmark).")
    warnings = list(a.limitations) + list(a.assumptions) + [f"uncertainty: {x}" for x in a.uncertainty.reasons]
    if len(set(r.served_models)) > 1 or any(m != "claude-opus-5-5" for m in r.served_models):
        warnings.append(f"served model(s): {', '.join(r.served_models)}")
    if warnings:
        st.subheader("Warnings and limitations")
        for w in warnings:
            st.warning(w)


def metrics_row(run: UIRun) -> dict:
    r = run.result
    return {"status": r.answer.status, "validation": r.answer.validation.status, "latency_s": round(r.latency_s, 2),
            "input_tokens": r.usage.input_tokens + r.usage.cache_read_input_tokens + r.usage.cache_creation_input_tokens,
            "output_tokens": r.usage.output_tokens, "tool_calls": r.counters.tool_calls,
            "llm_calls": r.counters.llm_calls, "est_cost_usd": round(r.usage.cost_usd, 4)}


def page_ask() -> None:
    st.header("Ask")
    db = st.selectbox("Database", UI_DATABASES, key="ask-db")
    sid = st.selectbox("System", UI_SYSTEMS, key="ask-system")
    q = st.text_input("Question", key="ask-q")
    if st.button("Run", key="ask-run") and q:
        llm = get_llm()
        if llm is None:
            st.error("No Anthropic credential configured: live questions are unavailable.")
            return
        st.session_state["ask-result"] = run_question(sid, db, q, llm)
    if "ask-result" in st.session_state:
        render_run(st.session_state["ask-result"], "ask")


def page_compare() -> None:
    st.header("Compare")
    db = st.selectbox("Database", UI_DATABASES, key="cmp-db")
    left = st.selectbox("Left system", UI_SYSTEMS, index=UI_SYSTEMS.index("b0"), key="cmp-left")
    right = st.selectbox("Right system", UI_SYSTEMS, index=UI_SYSTEMS.index("a2"), key="cmp-right")
    q = st.text_input("Question", key="cmp-q")
    if st.button("Compare", key="cmp-run") and q:
        llm = get_llm()
        if llm is None:
            st.error("No Anthropic credential configured: live questions are unavailable.")
            return
        order = [left, right]
        random.Random(q).shuffle(order)  # randomized, shown, sequential execution
        runs = {sid: run_question(sid, db, q, llm) for sid in order}
        st.session_state["cmp-result"] = (order, runs, left, right)
    if "cmp-result" in st.session_state:
        order, runs, left, right = st.session_state["cmp-result"]
        st.caption(f"Execution order: {' then '.join(order)} (sequential)")
        st.dataframe(pd.DataFrame({sid: metrics_row(runs[sid]) for sid in (left, right)}).astype(str), key="cmp-metrics")
        cols = st.columns(2)
        for col, sid in zip(cols, (left, right), strict=True):
            with col:
                st.subheader(sid.upper())
                render_run(runs[sid], f"cmp-{sid}")


def page_trace() -> None:
    st.header("Trace")
    uploaded = st.text_area("Paste a traces JSONL file (results/<experiment>/traces/*.jsonl)", key="trace-text")
    if uploaded:
        events = [json.loads(line) for line in uploaded.splitlines() if line.strip()]
        st.json(public_trace(events))


def main() -> None:
    st.set_page_config(page_title="Agentic SQL Analyst", layout="wide")
    st.title("Agentic SQL Analyst")
    page = st.sidebar.radio("Page", ["Ask", "Compare", "Trace"], key="page")
    {"Ask": page_ask, "Compare": page_compare, "Trace": page_trace}[page]()


main()
