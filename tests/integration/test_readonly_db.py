"""Read-only enforcement (layers L2-L4) and executor limits on SQLite and PostgreSQL."""
from __future__ import annotations

import pytest

from agent_sql.db.executor import Executor, ResultStore

ATTACKS_SQLITE = ["DELETE FROM orders", "UPDATE orders SET status = 'x'", "DROP TABLE orders",
                  "CREATE TABLE t (x INT)", "INSERT INTO regions VALUES (99, 'x', 'US')",
                  "SELECT email FROM customers", "SELECT sql FROM sqlite_master", "ATTACH DATABASE '/tmp/x.db' AS x",
                  "PRAGMA writable_schema = 1", "SELECT load_extension('x')"]
ATTACKS_PG = ["DELETE FROM orders", "UPDATE orders SET status = 'x'", "DROP TABLE orders", "CREATE TABLE t (x INT)",
              "INSERT INTO regions VALUES (99, 'x', 'US')", "SELECT email FROM customers",
              "SELECT phone FROM customers", "SET statement_timeout = 0; SELECT pg_sleep(1)",
              "SELECT * FROM pg_shadow", "SELECT pg_read_file('/etc/passwd')", "TRUNCATE orders"]


def _counts(db):
    ex = Executor(db, statement_timeout_s=30, max_rows=10)
    return ex.run("SELECT COUNT(*) FROM orders", ResultStore()).result.rows[0][0]


def test_defense_without_validator_sqlite(bi_dev_sqlite):
    """With the validator (L1) disabled, every attack still fails at L4."""
    before = _counts(bi_dev_sqlite)
    ex = Executor(bi_dev_sqlite, statement_timeout_s=5, max_rows=10, validate=False)
    store = ResultStore()
    for sql in ATTACKS_SQLITE:
        rec = ex.run(sql, store)
        assert not rec.ok, f"L4 admitted: {sql} -> {rec.result}"
    assert _counts(bi_dev_sqlite) == before


@pytest.mark.postgres
def test_defense_without_validator_postgres(bi_dev_postgres):
    before = _counts(bi_dev_postgres)
    ex = Executor(bi_dev_postgres, statement_timeout_s=5, max_rows=10, validate=False)
    store = ResultStore()
    for sql in ATTACKS_PG:
        rec = ex.run(sql, store)
        assert not rec.ok, f"L2 admitted: {sql} -> {rec.result}"
    assert _counts(bi_dev_postgres) == before


def test_validator_rejects_before_execution(bi_dev_any):
    rec = Executor(bi_dev_any, statement_timeout_s=5, max_rows=10).run("SELECT 1; DROP TABLE orders", ResultStore())
    assert rec.status == "REJECTED" and rec.validation["rule_violations"][0]["rule"] == "SR2"


def test_truncation_flag(bi_dev_any):
    rec = Executor(bi_dev_any, statement_timeout_s=30, max_rows=25).run("SELECT order_id FROM orders", ResultStore())
    assert rec.ok and rec.row_count == 25 and rec.result.truncated


def test_statement_timeout(bi_dev_any):
    ex = Executor(bi_dev_any, statement_timeout_s=0.5, max_rows=10)
    rec = ex.run("SELECT COUNT(*) FROM order_items a CROSS JOIN order_items b", ResultStore())
    assert rec.status == "TIMEOUT", rec.message


def test_error_classes(bi_dev_any):
    ex = Executor(bi_dev_any, statement_timeout_s=5, max_rows=10, validate=False)
    store = ResultStore()
    assert ex.run("SELECT nope FROM orders", store).error_class == "UNKNOWN_COLUMN"
    assert ex.run("SELECT 1 FROM nope_table", store).error_class in {"UNKNOWN_TABLE", "POLICY_VIOLATION"}


@pytest.mark.postgres
def test_results_identical_across_engines(bi_dev_sqlite, bi_dev_postgres):
    sql = ("SELECT o.status, COUNT(*) AS n, ROUND(SUM(oi.quantity * oi.unit_price), 2) AS gross "
           "FROM orders o JOIN order_items oi ON oi.order_id = o.order_id GROUP BY o.status ORDER BY o.status")
    a = Executor(bi_dev_sqlite, statement_timeout_s=30, max_rows=100).run(sql, ResultStore()).result.rows
    b = Executor(bi_dev_postgres, statement_timeout_s=30, max_rows=100).run(sql, ResultStore()).result.rows
    assert [(s, n, round(float(g), 2)) for s, n, g in a] == [(s, n, round(float(g), 2)) for s, n, g in b]
