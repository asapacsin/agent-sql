from agent_sql.safety.validator import validate_sql


def test_unknown_column_hint(toy_policy):
    r = validate_sql("SELECT ordr_date FROM orders", toy_policy)
    assert r.error_class == "UNKNOWN_COLUMN" and "order_date" in (r.hint() or "")


def test_unknown_table_hint(toy_policy):
    r = validate_sql("SELECT 1 FROM order", toy_policy) if False else validate_sql("SELECT 1 FROM ordrs", toy_policy)
    assert r.error_class == "UNKNOWN_TABLE" and "orders" in (r.hint() or "")


def test_cartesian_warning(toy_policy):
    r = validate_sql("SELECT o.order_id FROM orders o, customers c", toy_policy)
    assert r.ok and any(w["code"] == "CARTESIAN_JOIN" for w in r.warnings)


def test_length_limit(toy_policy):
    assert validate_sql("SELECT 1 " + " " * 20001, toy_policy).violations[0].rule == "SR10"


def test_referenced_tables_reported(toy_policy):
    r = validate_sql("SELECT o.order_id FROM orders o JOIN order_items i ON i.order_id = o.order_id", toy_policy)
    assert r.ok and set(r.referenced_tables) == {"orders", "order_items"}
