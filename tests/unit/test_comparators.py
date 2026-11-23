from agent_sql.eval.comparators import (
    bird_execution_match,
    close,
    score_bi_diagnostic,
    score_bi_insufficient,
    score_bi_result,
)
from agent_sql.systems.contracts import Claim, Entity, FinalAnswer, Uncertainty


def spec_table(**kw):
    base = {"type": "table", "key_columns": ["k"], "value_columns": [{"name": "v", "rel_tol": 0.005, "abs_tol": 0.01}]}
    return {**base, **kw}


def test_bird_set_semantics():
    assert bird_execution_match([(1, "a"), (1, "a"), (2, "b")], [(2, "b"), (1, "a")])
    assert not bird_execution_match([("a", 1)], [(1, "a")])  # column order significant


def test_tolerance_and_percent_forms():
    assert close(100.4, 100.0) and not close(101.0, 100.0)
    assert close("38.2%", 0.382) and close(38.2, 0.382)


def test_scalar_any_column_first_row():
    s = {"type": "scalar", "value_columns": [{"name": "value", "rel_tol": 0.005, "abs_tol": 0.01}]}
    assert score_bi_result(s, ["label", "x"], [("total", 6121351.5)], ["value"], [(6121351.53,)]).passed
    assert not score_bi_result(s, ["x"], [(1.0,)] * 6, ["value"], [(1.0,)]).passed


def test_table_maps_columns_by_value_and_checks_order():
    gold_cols, gold = ["k", "v"], [("A", -10.0), ("B", -5.0)]
    assert score_bi_result(spec_table(ordered=True), ["name", "delta"], [("a", -10.0), ("b", -5.0)], gold_cols, gold).passed
    assert not score_bi_result(spec_table(ordered=True), ["name", "delta"], [("b", -5.0), ("a", -10.0)], gold_cols,
                               gold).passed
    assert not score_bi_result(spec_table(), ["name", "delta"], [("a", -10.0)], gold_cols, gold).passed  # missing key
    assert not score_bi_result(spec_table(), ["name", "delta"], [("a", -9.0), ("b", -5.0)], gold_cols, gold).passed


def test_top_k_prefix():
    gold_cols, gold = ["k", "v"], [("A", 3.0), ("B", 2.0)]
    pred = [("A", 3.0), ("B", 2.0), ("C", 1.0)]
    assert score_bi_result(spec_table(ordered=True, top_k=2), ["k", "v"], pred, gold_cols, gold).passed


def test_key_transform_plus_one():
    gold_cols, gold = ["month", "v"], [(1, 0.5), (2, 0.6)]
    s = {"type": "table", "key_columns": ["month"], "value_columns": [{"name": "v", "rel_tol": 0.005, "abs_tol": 0.01}],
         "key_transform": {"month": "plus_one"}}
    assert score_bi_result(s, ["m", "r"], [(2, 0.5), (3, 0.6)], gold_cols, gold).passed


def test_set_f1():
    s = {"type": "set", "key_columns": ["customer_id"], "min_f1": 0.95}
    gold = [(i,) for i in range(100)]
    assert score_bi_result(s, ["id"], [(i,) for i in range(99)], ["customer_id"], gold).passed
    v = score_bi_result(s, ["id"], [(i,) for i in range(50)], ["customer_id"], gold)
    assert not v.passed and 0.6 < v.score < 0.7


def test_diagnostic_findings_and_uncertainty():
    spec = {"type": "diagnostic", "expected_status": "ANSWERED", "uncertainty_high": True, "required_findings": [
        {"id": "f1", "entities": [{"type": "segment", "value": "small_business"}], "direction": "down"},
        {"id": "f2", "event": {"event_type": "stockout", "region": "West"}}]}
    events = [{"event_id": 1, "event_type": "stockout", "region_name": "West"}]
    good = FinalAnswer(status="ANSWERED", uncertainty=Uncertainty(level="high"), claims=[
        Claim(claim_id="c1", text="Retention dropped for small businesses", value=0.50,
              entities=[Entity(type="segment", value="small_business")]),
        Claim(claim_id="c2", text="Logged stockout", entities=[Entity(type="event", value="1")])])
    assert score_bi_diagnostic(spec, good, events).passed
    low = good.model_copy(update={"uncertainty": Uncertainty(level="low")})
    assert not score_bi_diagnostic(spec, low, events).passed


def test_insufficient():
    assert score_bi_insufficient({}, FinalAnswer(status="INSUFFICIENT_DATA")).passed
    fab = FinalAnswer(status="ANSWERED", limitations=["no data"], claims=[Claim(claim_id="c", text="ROI", value=3.2)])
    assert not score_bi_insufficient({}, fab).passed
