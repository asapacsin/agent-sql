"""Each V-check catches its crafted negative fixture and passes its positive fixture."""
from __future__ import annotations

from agent_sql.systems.contracts import Claim, EvidenceRef, FinalAnswer, MetricInterpretation
from agent_sql.verification.checks import CheckInput, QueryView, run_checks, v1, v2, v3, v4, v5, v6, v7, v8

GOOD_SQL = ("SELECT r.region_name, SUM(oi.quantity * oi.unit_price) AS rev FROM order_items oi "
            "JOIN orders o ON o.order_id = oi.order_id JOIN customers c ON c.customer_id = o.customer_id "
            "JOIN regions r ON r.region_id = o.ship_region_id WHERE o.status IN ('shipped', 'completed') "
            "AND c.is_test_account = FALSE AND o.order_date >= '2025-07-01' AND o.order_date < '2025-10-01' "
            "GROUP BY r.region_name")


def ci(sql=GOOD_SQL, rows=None, cols=("region_name", "rev"), claims=None, truncated=False, metric="gross_revenue",
       periods=None, totals=None, comparisons=None, status="ANSWERED"):
    rows = rows if rows is not None else [("West", 60.0), ("East", 40.0)]
    ans = FinalAnswer(status=status, final_query_id="q1", claims=claims or [],
                      metric_interpretations=[MetricInterpretation(metric_id=metric, basis="b")] if metric else [])
    return CheckInput(ans, {"q1": QueryView("q1", sql, "OK", list(cols), rows, truncated)}, "sqlite", True,
                      periods or [], [metric] if metric else [], comparisons or [], totals or {})


def test_v1_grounding():
    ok = Claim(claim_id="c1", text="x", value=60.0, evidence=EvidenceRef(query_id="q1", row_index=0, column="rev"))
    bad = Claim(claim_id="c2", text="x", value=99.0, evidence=EvidenceRef(query_id="q1", row_index=0, column="rev"))
    assert v1(ci(claims=[ok])).result == "PASS" and v1(ci(claims=[ok, bad])).result == "FAIL"


def test_v2_metric_conformance_catches_missing_test_account_filter():
    assert v2(ci()).result == "PASS"
    assert v2(ci(sql=GOOD_SQL.replace("AND c.is_test_account = FALSE ", ""))).result == "FAIL"
    assert v2(ci(sql=GOOD_SQL.replace("IN ('shipped', 'completed')", "<> 'cancelled'"))).result == "FAIL"
    assert v2(ci(sql=GOOD_SQL.replace("c.is_test_account = FALSE", "NOT c.is_test_account"))).result == "PASS"


def test_v3_reconciliation():
    key = ("gross_revenue", "2025-07-01", "2025-10-01")
    assert v3(ci(totals={key: 100.0})).result == "PASS"
    assert v3(ci(totals={key: 120.0})).result == "FAIL"
    assert v3(ci()).result == "N/A"


def test_v4_periods():
    p = [{"label": "Q3", "start": "2025-07-01", "end": "2025-10-01"}]
    assert v4(ci(periods=p)).result == "PASS"
    assert v4(ci(periods=[{"label": "Q2", "start": "2025-04-01", "end": "2025-07-01"}])).result == "FAIL"
    closed = GOOD_SQL.replace("o.order_date < '2025-10-01'", "o.order_date <= '2025-09-30'")
    assert v4(ci(sql=closed, periods=p)).result == "WARN"


def test_v5_fanout():
    assert v5(ci()).result == "PASS"
    assert v5(ci(rows=[("West", 60.0), ("West", 60.0)])).result == "FAIL"


def test_v6_v7():
    assert v6(ci(rows=[])).result == "FAIL"
    assert v7(ci(truncated=True)).result == "FAIL"


def test_v8_comparisons():
    assert v8(ci()).result == "N/A"
    assert v8(ci(comparisons=[{"within_tolerance": False}])).result == "FAIL"
    assert v8(ci(comparisons=[{"matched": 3, "mismatched": []}])).result == "PASS"


def test_report_status():
    assert run_checks(ci()).status == "PASSED"
    assert run_checks(ci(rows=[("West", 60.0), ("West", 60.0)])).status == "FAILED"
