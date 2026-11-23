"""Load a generated RetailCo instance into PostgreSQL and (re)create the read-only role `agent_ro`.

    AGENT_SQL_PG_ADMIN_URL=postgresql://admin:pw@localhost:5432/postgres \
    python3 -m bi_dataset.load_postgres --instance dev

Creates database `retailco_<instance>` with schema `bi`. `agent_ro` gets USAGE on `bi`, SELECT on every table,
and column-level SELECT on `customers` that excludes restricted columns (docs/spec/TOOLS.md layer L2).
The agent_ro password comes from AGENT_SQL_PG_RO_PASSWORD (local development default below; never a real secret).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg
import yaml
from psycopg import sql

from bi_dataset.generate import PKG, TABLE_ORDER, default_out

RO_ROLE = "agent_ro"
LOCAL_DEV_RO_PASSWORD = "agent_ro_local_only"  # noqa: S105 - local throwaway role, not a credential


def ro_password() -> str:
    return os.environ.get("AGENT_SQL_PG_RO_PASSWORD", LOCAL_DEV_RO_PASSWORD)


def admin_url() -> str:
    url = os.environ.get("AGENT_SQL_PG_ADMIN_URL")
    if not url:
        raise SystemExit("AGENT_SQL_PG_ADMIN_URL is not set")
    return url


def _with_db(url: str, dbname: str) -> str:
    base, _, _ = url.rpartition("/")
    return f"{base}/{dbname}"


def agent_url(instance: str, base_admin_url: str | None = None) -> str:
    """Connection URL for agent_ro on the instance database, derived from the admin URL's host/port."""
    url = base_admin_url or admin_url()
    rest = url.split("://", 1)[1]
    hostpart = rest.split("@", 1)[1] if "@" in rest else rest
    host = hostpart.split("/", 1)[0]
    return f"postgresql://{RO_ROLE}:{ro_password()}@{host}/retailco_{instance}"


def load(instance: str, src: Path | None = None) -> str:
    src = src or default_out(instance)
    csv_dir = src / "csv"
    if not csv_dir.is_dir():
        raise SystemExit(f"generate the instance first: {csv_dir} missing")
    dbname = f"retailco_{instance}"
    url = admin_url()
    with psycopg.connect(url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        role = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (RO_ROLE,)).fetchone()
        if role:
            conn.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(RO_ROLE),
                                                                          sql.Literal(ro_password())))
        else:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(RO_ROLE),
                                                                           sql.Literal(ro_password())))
        for setting, value in (("default_transaction_read_only", "on"), ("statement_timeout", "30s"),
                               ("idle_in_transaction_session_timeout", "60s"), ("search_path", "bi")):
            conn.execute(sql.SQL("ALTER ROLE {} SET {} = {}").format(sql.Identifier(RO_ROLE), sql.SQL(setting),
                                                                    sql.Literal(value)))
    desc = yaml.safe_load((PKG / "descriptions.yaml").read_text())
    restricted = {t: set(cols) for t, cols in desc.get("restricted", {}).items()}
    with psycopg.connect(_with_db(url, dbname)) as conn:
        conn.execute("DROP SCHEMA IF EXISTS bi CASCADE")
        conn.execute("CREATE SCHEMA bi")
        conn.execute("SET search_path = bi")
        conn.execute((PKG / "schema.sql").read_text())
        for name in TABLE_ORDER:
            with open(csv_dir / f"{name}.csv", encoding="utf-8") as f:
                header = f.readline().strip()
                cols = sql.SQL(", ").join(sql.Identifier(c) for c in header.split(","))
                stmt = sql.SQL("COPY {} ({}) FROM STDIN WITH (FORMAT csv, NULL '')").format(sql.Identifier(name), cols)
                with conn.cursor().copy(stmt) as cp:
                    for chunk in iter(lambda: f.read(1 << 20), ""):
                        cp.write(chunk)
        for table, text in desc.get("tables", {}).items():
            conn.execute(sql.SQL("COMMENT ON TABLE {} IS {}").format(sql.Identifier(table), sql.Literal(text)))
        for table, cols in desc.get("columns", {}).items():
            for col, text in cols.items():
                conn.execute(sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(
                    sql.Identifier(table), sql.Identifier(col), sql.Literal(str(text))))
        # privileges: nothing on public, read-only on bi, column grants on restricted tables
        conn.execute(sql.SQL("REVOKE ALL ON SCHEMA public FROM {}").format(sql.Identifier(RO_ROLE)))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(dbname)))
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname),
                                                                         sql.Identifier(RO_ROLE)))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA bi TO {}").format(sql.Identifier(RO_ROLE)))
        for name in TABLE_ORDER:
            if name in restricted:
                cols = [r[0] for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema='bi' AND table_name=%s "
                    "ORDER BY ordinal_position", (name,)).fetchall() if r[0] not in restricted[name]]
                conn.execute(sql.SQL("GRANT SELECT ({}) ON {} TO {}").format(
                    sql.SQL(", ").join(sql.Identifier(c) for c in cols), sql.Identifier(name),
                    sql.Identifier(RO_ROLE)))
            else:
                conn.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(sql.Identifier(name),
                                                                       sql.Identifier(RO_ROLE)))
        conn.commit()
    return dbname


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default="dev")
    ap.add_argument("--src", type=Path)
    args = ap.parse_args(argv)
    print("loaded", load(args.instance, args.src))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
