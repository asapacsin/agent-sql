"""Single-result answer composer used by B0/B1/B1-SC on BI tasks (one LLM call, no queries)."""
from __future__ import annotations

import json

from agent_sql.config.prompts import Prompt
from agent_sql.db.executor import QueryRecord
from agent_sql.systems.base import RunContext
from agent_sql.systems.contracts import (
    ChartHint,
    Claim,
    Entity,
    EvidenceRef,
    FinalAnswer,
    MetricInterpretation,
    Uncertainty,
)

ENTITY_TYPES = ["region", "category", "department", "product", "customer", "segment", "event", "period", "channel",
                "other"]
COMPOSER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["answer_text", "claims", "metric_interpretations", "assumptions", "limitations", "uncertainty",
                 "chart"],
    "properties": {
        "answer_text": {"type": "string"},
        "claims": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["text", "value", "unit", "entities", "row_index", "column"],
            "properties": {
                "text": {"type": "string"}, "value": {"type": ["number", "string", "null"]},
                "unit": {"type": ["string", "null"]},
                "entities": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["type", "value"],
                    "properties": {"type": {"type": "string", "enum": ENTITY_TYPES}, "value": {"type": "string"}}}},
                "row_index": {"type": ["integer", "null"]}, "column": {"type": ["string", "null"]}}}},
        "metric_interpretations": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["metric_id", "basis", "period"],
            "properties": {"metric_id": {"type": "string"}, "basis": {"type": "string"},
                           "period": {"type": ["string", "null"]}}}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "limitations": {"type": "array", "items": {"type": "string"}},
        "uncertainty": {"type": "object", "additionalProperties": False, "required": ["level", "reasons"],
                        "properties": {"level": {"type": "string", "enum": ["low", "medium", "high"]},
                                       "reasons": {"type": "array", "items": {"type": "string"}}}},
        "chart": {"type": ["object", "null"], "additionalProperties": False, "required": ["kind", "x", "y"],
                  "properties": {"kind": {"type": "string", "enum": ["bar", "line", "table"]},
                                 "x": {"type": ["string", "null"]}, "y": {"type": ["string", "null"]}}},
    },
}


def compose(ctx: RunContext, prompt: Prompt, rec: QueryRecord, context: str) -> FinalAnswer:
    lim = ctx.rt.limits
    assert rec.result is not None
    rows = rec.result.rows[: lim.observation_max_rows]
    rows_txt = "\n".join(json.dumps([(v[: lim.observation_max_cell_chars] if isinstance(v, str) else v) for v in r],
                                    default=str) for r in rows)
    system = prompt.render(question=ctx.task.question, context=context, sql=rec.sql, row_count=str(rec.row_count),
                           truncated=", TRUNCATED" if rec.result.truncated else "",
                           columns=", ".join(rec.result.columns), rows=rows_txt or "(no rows)")
    resp = ctx.call_llm(system=system, messages=[{"role": "user", "content": "Write the final answer JSON."}],
                        output_schema=COMPOSER_SCHEMA, purpose="compose answer")
    try:
        data = json.loads(resp.text())
    except json.JSONDecodeError:
        return FinalAnswer.failure("FAILED_INVALID_OUTPUT", "composer returned invalid JSON")
    claims = []
    for i, c in enumerate(data.get("claims", [])):
        ev = None
        if c.get("row_index") is not None and c.get("column"):
            ev = EvidenceRef(query_id=rec.query_id, row_index=int(c["row_index"]), column=str(c["column"]))
        claims.append(Claim(claim_id=f"c{i + 1}", text=c.get("text", ""), value=c.get("value"), unit=c.get("unit"),
                            entities=[Entity(**e) for e in c.get("entities", [])], evidence=ev))
    chart = data.get("chart")
    return FinalAnswer(
        status="ANSWERED", answer_text=str(data.get("answer_text", ""))[:1200], final_query_id=rec.query_id,
        claims=claims, tables=[rec.query_id],
        charts=[ChartHint(query_id=rec.query_id, **chart)] if chart else [],
        metric_interpretations=[MetricInterpretation(**m) for m in data.get("metric_interpretations", [])],
        assumptions=list(data.get("assumptions", [])), limitations=list(data.get("limitations", [])),
        uncertainty=Uncertainty(**data.get("uncertainty", {"level": "medium", "reasons": []})))
