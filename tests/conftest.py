from __future__ import annotations

import pytest

from agent_sql.safety.policy import AccessPolicy

BI_TABLES = {
    "orders": {"order_id": "INT", "customer_id": "INT", "order_date": "DATE", "status": "TEXT",
               "ship_region_id": "INT", "channel": "TEXT", "promotion_id": "INT"},
    "order_items": {"order_item_id": "INT", "order_id": "INT", "product_id": "INT", "quantity": "INT",
                    "unit_price": "DOUBLE", "discount_amount": "DOUBLE"},
    "customers": {"customer_id": "INT", "customer_name": "TEXT", "email": "TEXT", "phone": "TEXT",
                  "segment": "TEXT", "is_test_account": "INT", "home_region_id": "INT"},
    "products": {"product_id": "INT", "product_name": "TEXT", "category_id": "INT", "description": "TEXT"},
}


@pytest.fixture(params=["sqlite", "postgres"])
def toy_policy(request) -> AccessPolicy:
    return AccessPolicy(dialect=request.param, tables=BI_TABLES,
                        restricted={"customers": {"email", "phone"}},
                        schema="bi" if request.param == "postgres" else None)


import os  # noqa: E402

from agent_sql.db.registry import bi_sqlite_path, open_bi  # noqa: E402

PG_ADMIN = os.environ.get("AGENT_SQL_PG_ADMIN_URL")


@pytest.fixture(scope="session")
def bi_dev_sqlite():
    if not bi_sqlite_path("dev").exists():
        pytest.skip("RetailCo-dev not generated: python3 -m bi_dataset.generate --instance dev")
    return open_bi("dev", "sqlite")


@pytest.fixture(scope="session")
def bi_dev_postgres():
    if not PG_ADMIN:
        pytest.skip("AGENT_SQL_PG_ADMIN_URL not set")
    return open_bi("dev", "postgres")


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def bi_dev_any(request):
    if request.param == "postgres":
        if not PG_ADMIN:
            pytest.skip("AGENT_SQL_PG_ADMIN_URL not set")
        return open_bi("dev", "postgres")
    if not bi_sqlite_path("dev").exists():
        pytest.skip("RetailCo-dev not generated")
    return open_bi("dev", "sqlite")
