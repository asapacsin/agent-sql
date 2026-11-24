"""A generic, deterministic FakeLLM policy that drives ANY system through its protocol (tests only)."""
from __future__ import annotations

import json

SQL = ("SELECT o.status, COUNT(*) AS n FROM orders o JOIN customers c ON c.customer_id = o.customer_id "
       "WHERE c.is_test_account = FALSE GROUP BY o.status")


def _last_query_id(messages) -> str | None:  # noqa: ANN001
    qid = None
    for m in messages:
        if m["role"] == "user" and isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result":
                    try:
                        payload = json.loads(b["content"])
                    except (TypeError, ValueError):
                        continue
                    if payload.get("status") == "OK" and payload.get("query_id"):
                        qid = payload["query_id"]
    return qid


def answer(qid):  # noqa: ANN001, ANN201
    return {"status": "ANSWERED", "answer_text": "Order counts by status.", "final_query_id": qid,
            "answer_columns": ["status", "n"],
            "claims": [{"text": "orders by status", "value": None, "unit": None, "entities": [], "query_id": qid,
                        "row_index": 0, "column": "n"}], "charts": [], "metric_interpretations": [],
            "assumptions": [], "limitations": [], "uncertainty": {"level": "medium", "reasons": []}}


def policy(req, i):  # noqa: ANN001, ANN201
    if req.output_schema is not None:
        if "sql" in req.output_schema["properties"]:
            return json.dumps({"sql": SQL})
        return json.dumps({"answer_text": "a", "claims": [], "metric_interpretations": [], "assumptions": [],
                           "limitations": [], "uncertainty": {"level": "medium", "reasons": []}, "chart": None})
    names = {t.name for t in req.tools}
    qid = _last_query_id(req.messages)
    step_done = any("complete_step" in json.dumps(m["content"], default=str) and m["role"] == "assistant"
                    for m in req.messages)
    uid = f"toolu_{len(req.messages)}"

    def call(name, args):  # noqa: ANN001, ANN202
        return {"content": [{"type": "tool_use", "id": uid, "name": name, "input": args}], "stop_reason": "tool_use"}

    if "submit_plan" in names and "complete_step" not in names:
        return call("submit_plan", {"steps": [{"step_id": "s1", "goal": "count", "expected_output": "table",
                                               "metric_ids": ["orders"], "depends_on": []}], "periods": [],
                                    "assumptions": []})
    if qid is None and "execute_readonly_sql" in names:
        return call("execute_readonly_sql", {"purpose": "count by status", "sql": SQL, "label": None})
    if "complete_step" in names and not step_done and qid:
        return call("complete_step", {"step_id": "s1", "query_id": qid, "summary": "counted",
                                      "unanswerable_reason": None})
    if "submit_answer" in names:
        return call("submit_answer", answer(qid))
    return call("execute_readonly_sql", {"purpose": "count by status", "sql": SQL, "label": None})
