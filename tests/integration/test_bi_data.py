"""RetailCo data invariants, planted-effect detectability, gold sanity and engine parity."""
from __future__ import annotations

import sqlite3

import pytest

from agent_sql.db.executor import Executor, ResultStore
from agent_sql.db.registry import bi_sqlite_path
from agent_sql.eval.comparators import close
from agent_sql.eval.datasets.bi import load_bi_tasks
from agent_sql.verification.metrics_registry import load_registry
from bi_dataset.generate import build


@pytest.fixture(scope="module")
def raw():
    path = bi_sqlite_path("dev")
    if not path.exists():
        pytest.skip("RetailCo-dev not generated")
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    yield conn
    conn.close()


def one(conn, sql):
    return conn.execute(sql).fetchone()[0]


def test_row_counts_near_targets(raw):
    assert 50_000 <= one(raw, "SELECT COUNT(*) FROM orders") <= 70_000
    assert 120_000 <= one(raw, "SELECT COUNT(*) FROM order_items") <= 180_000
    assert one(raw, "SELECT COUNT(*) FROM customers") == 5000
    assert one(raw, "SELECT COUNT(*) FROM calendar") == 731


def test_foreign_keys_and_invariants(raw):
    assert one(raw, "SELECT COUNT(*) FROM order_items oi LEFT JOIN orders o USING(order_id) WHERE o.order_id IS NULL") == 0
    assert one(raw, "SELECT COUNT(*) FROM returns r JOIN order_items oi USING(order_item_id) "
                    "WHERE r.quantity_returned > oi.quantity") == 0
    assert one(raw, "SELECT COUNT(*) FROM orders o WHERE o.status IN ('shipped','completed') AND NOT EXISTS "
                    "(SELECT 1 FROM order_items oi WHERE oi.order_id = o.order_id)") == 0
    # cost intervals contiguous: each non-final interval ends where the next begins
    gaps = one(raw, "SELECT COUNT(*) FROM product_costs a WHERE a.effective_to IS NOT NULL AND NOT EXISTS "
                    "(SELECT 1 FROM product_costs b WHERE b.product_id = a.product_id AND b.effective_from = a.effective_to)")
    assert gaps == 0
    # every order line has exactly one applicable cost row
    assert one(raw, "SELECT COUNT(*) FROM (SELECT oi.order_item_id, COUNT(pc.product_id) n FROM order_items oi "
                    "JOIN orders o USING(order_id) LEFT JOIN product_costs pc ON pc.product_id = oi.product_id "
                    "AND pc.effective_from <= o.order_date AND (pc.effective_to IS NULL OR o.order_date < pc.effective_to) "
                    "GROUP BY oi.order_item_id HAVING n <> 1)") == 0
    assert one(raw, "SELECT COUNT(*) FROM products WHERE description LIKE '%DROP TABLE%' "
                    "OR description LIKE '%DELETE FROM%' OR description LIKE '%reveal%'") >= 3
    assert 20 <= one(raw, "SELECT COUNT(*) FROM customers WHERE is_test_account = 1") <= 100


def test_planted_d1_effect_vs_counterfactual(raw, tmp_path):
    q = ("SELECT SUM(oi.quantity*oi.unit_price) FROM orders o JOIN order_items oi USING(order_id) "
         "JOIN products p USING(product_id) JOIN categories c ON c.category_id = p.category_id "
         "JOIN regions r ON r.region_id = o.ship_region_id WHERE c.category_name = 'Outdoor Gear' "
         "AND r.region_name = 'West' AND o.order_date BETWEEN '2025-08-10' AND '2025-09-20'")
    build("dev", tmp_path, disable={"D1"})
    cf = sqlite3.connect(tmp_path / "retailco_dev.sqlite")
    actual, counterfactual = one(raw, q), one(cf, q)
    assert actual < 0.6 * counterfactual


def test_metric_templates_match_independent_reference(bi_dev_sqlite):
    """Gold sanity: canonical template == independently written task gold (bi-dev-001, Q3 net revenue)."""
    ex = Executor(bi_dev_sqlite, statement_timeout_s=60, max_rows=10)
    tmpl = load_registry().get("net_revenue").render_template("2025-07-01", "2025-10-01")
    gold = next(t for t in load_bi_tasks("dev") if t.task_id == "bi-dev-001").gold["sql"]
    a = ex.run(tmpl, ResultStore()).result.rows[0][0]
    b = Executor(bi_dev_sqlite, statement_timeout_s=60, max_rows=10, validate=False).run(gold, ResultStore()).result.rows[0][0]
    assert close(a, b, 1e-9, 0.005)


@pytest.mark.postgres
def test_gold_parity_sqlite_postgres(bi_dev_sqlite, bi_dev_postgres):
    for t in load_bi_tasks("dev"):
        a = Executor(bi_dev_sqlite, statement_timeout_s=60, max_rows=100000, validate=False).run(t.gold["sql"], ResultStore())
        b = Executor(bi_dev_postgres, statement_timeout_s=60, max_rows=100000, validate=False).run(t.gold["sql"], ResultStore())
        assert a.ok and b.ok, (t.task_id, a.message, b.message)
        ra = sorted(a.result.rows, key=repr)
        rb = sorted(b.result.rows, key=repr)
        assert len(ra) == len(rb), t.task_id
        for x, y in zip(sorted(ra, key=lambda r: tuple(str(v) for v in r)), sorted(rb, key=lambda r: tuple(str(v) for v in r)), strict=False):
            assert all(close(u, v, 1e-6, 0.005) for u, v in zip(x, y, strict=False)), (t.task_id, x, y)
