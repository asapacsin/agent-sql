from agent_sql.eval.failure_taxonomy import auto_failure_code
from agent_sql.systems.contracts import Claim, EvidenceRef
from agent_sql.tracing.events import Trace
from agent_sql.tracing.redact import redact_text
from agent_sql.verification.grounding import check_grounding, claim_matches_cell
from agent_sql.verification.metrics_registry import load_registry


def test_claim_cell_matching():
    assert claim_matches_cell(6121351.53, 6121351.5312)
    assert claim_matches_cell(38.2, 0.3816)  # percent display of a fraction
    assert claim_matches_cell(6.12, 6.1213)  # rounded display
    assert not claim_matches_cell(7.0, 6.1)


def test_grounding_check():
    claims = [Claim(claim_id="c1", text="x", value=5, evidence=EvidenceRef(query_id="q1", row_index=0, column="v")),
              Claim(claim_id="c2", text="y", value=9)]
    check, bad = check_grounding(claims, lambda q, r, c: (True, 5))
    assert check.result == "FAIL" and bad == ["c2"]


def test_failure_codes():
    kw = dict(success=False, task_type="aggregate", last_error_class=None, pred_empty=False, gold_empty=False,
              tables_exact=True, abstained=False, numeric_claims_on_insufficient=False, ungrounded_claims=False)
    assert auto_failure_code(status="FAILED_EXECUTION", **{**kw, "last_error_class": "UNKNOWN_COLUMN"}) == "F-SCHEMA-COLUMN"
    assert auto_failure_code(status="FAILED_REPAIR_BUDGET", **kw) == "F-BUDGET"
    assert auto_failure_code(status="ANSWERED", **{**kw, "task_type": "insufficient",
                                                   "numeric_claims_on_insufficient": True}) == "F-FABRICATION"
    assert auto_failure_code(status="ANSWERED", **{**kw, "success": True}) is None


def test_trace_strips_thinking_and_redacts():
    t = Trace("r", "t", "s")
    e = t.emit("llm_call", content=[{"type": "thinking", "thinking": "secret"}, {"type": "text", "text": "hi"}],
               note="key sk-ant-abcdefghijklmnop")
    assert e["content"] == [{"type": "text", "text": "hi"}] and "sk-ant" not in e["note"]
    assert redact_text("postgresql://u:pw@h/db") == "postgresql://u:[REDACTED]@h/db"


def test_glossary_has_definitions_not_sql():
    reg = load_registry()
    g = reg.glossary()
    assert "net_revenue" in g and "SELECT" not in g and "JOIN" not in g
    expected = {"gross_revenue", "discounts", "refunds", "net_revenue", "cogs", "gross_profit", "gross_margin", "orders",
                "aov", "units_sold", "return_rate", "active_customers", "new_customers", "retention_mom", "churn_mom",
                "purchase_frequency", "declining_frequency_customer", "growth_rate", "absolute_change", "revenue_share"}
    assert set(reg.metrics) == expected
