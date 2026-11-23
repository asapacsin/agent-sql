"""E3 fault-injection database variants of the frozen RetailCo-test instance (docs/spec/EXPERIMENTS.md section 10).

    python3 -m bi_dataset.variants            # writes data/generated/retailco_test/variants/{drift,casing}.agent.sqlite

FM2 drift : renames orders.order_date->ordered_on, order_items.unit_price->sale_price, orders.ship_region_id->ship_region;
            systems still receive the pre-drift documentation (stale docs).
FM3 casing: order status literals stored in title case ('Completed'); documentation unchanged.
The frozen test instance itself is never modified.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

from bi_dataset.generate import default_out

RENAMES = [("orders", "order_date", "ordered_on"), ("order_items", "unit_price", "sale_price"),
           ("orders", "ship_region_id", "ship_region")]


def variant_dir() -> Path:
    return default_out("test") / "variants"


def build() -> dict[str, Path]:
    src = default_out("test") / "retailco_test.agent.sqlite"
    out = variant_dir()
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    drift = out / "drift.agent.sqlite"
    shutil.copyfile(src, drift)
    conn = sqlite3.connect(drift)
    for table, old, new in RENAMES:
        conn.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    conn.commit()
    conn.close()
    paths["drift"] = drift
    casing = out / "casing.agent.sqlite"
    shutil.copyfile(src, casing)
    conn = sqlite3.connect(casing)
    conn.execute("UPDATE orders SET status = upper(substr(status, 1, 1)) || substr(status, 2)")
    conn.commit()
    conn.close()
    paths["casing"] = casing
    return paths


def main() -> int:
    for k, v in build().items():
        print(k, v)
    return 0


if __name__ == "__main__":
    sys.exit(main())
