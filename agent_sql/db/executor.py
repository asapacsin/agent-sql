"""Database handle, result store, and the single read-only executor used by every system and the evaluator."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from agent_sql.db.catalog import Catalog, load_catalog
from agent_sql.db.engines import SqliteGuard, postgres_engines, sqlite_engines
from agent_sql.safety.policy import AccessPolicy
from agent_sql.safety.validator import ValidationResult, validate_sql


@dataclass
class Database:
    db_id: str
    dialect: str  # "sqlite" | "postgres"
    agent_engine: Engine
    catalog: Catalog
    schema: str | None = None
    guard: SqliteGuard | None = None
    snapshot_hash: str | None = None
    restricted: dict[str, set[str]] = field(default_factory=dict)
    context_catalog: Catalog | None = None  # documentation shown to systems (E3 FM2: stale docs); default = catalog

    @property
    def doc_catalog(self) -> Catalog:
        return self.context_catalog or self.catalog

    def policy(self) -> AccessPolicy:
        tables = {name: {c.name.lower(): c.type for c in t.columns} for name, t in self.catalog.tables.items()}
        return AccessPolicy(self.dialect, tables, self.restricted, self.schema)

    @classmethod
    def from_sqlite(cls, db_id: str, path: Path, *, restricted: dict[str, set[str]] | None = None,
                    descriptions: dict | None = None, snapshot_hash: str | None = None) -> Database:
        agent, catalog_engine, guard = sqlite_engines(Path(path), restricted)
        d = descriptions or {}
        catalog = load_catalog(catalog_engine, None, table_descriptions=d.get("tables"),
                               column_descriptions=d.get("columns"), restricted=restricted,
                               documented_fks=d.get("documented_fks"))
        catalog_engine.dispose()
        return cls(db_id, "sqlite", agent, catalog, None, guard, snapshot_hash,
                   {k.lower(): {c.lower() for c in v} for k, v in (restricted or {}).items()})

    @classmethod
    def from_postgres(cls, db_id: str, agent_url: str, schema: str, *, restricted: dict[str, set[str]] | None = None,
                      descriptions: dict | None = None, snapshot_hash: str | None = None) -> Database:
        agent, catalog_engine = postgres_engines(agent_url)
        d = descriptions or {}
        catalog = load_catalog(catalog_engine, schema, table_descriptions=d.get("tables"),
                               column_descriptions=d.get("columns"), restricted=restricted,
                               documented_fks=d.get("documented_fks"))
        catalog_engine.dispose()
        return cls(db_id, "postgres", agent, catalog, schema, None, snapshot_hash,
                   {k.lower(): {c.lower() for c in v} for k, v in (restricted or {}).items()})


@dataclass
class QueryResult:
    columns: list[str]
    column_types: list[str]
    rows: list[tuple]
    truncated: bool


@dataclass
class QueryRecord:
    query_id: str
    sql: str
    label: str | None
    status: str  # OK | ERROR | TIMEOUT | REJECTED
    error_class: str | None = None
    message: str | None = None
    hint: str | None = None
    result: QueryResult | None = None
    elapsed_ms: int = 0
    validation: dict | None = None

    @property
    def ok(self) -> bool:
        return self.status == "OK"

    @property
    def row_count(self) -> int:
        return len(self.result.rows) if self.result else 0

    def summary(self) -> dict:
        return {"query_id": self.query_id, "label": self.label, "sql": self.sql, "status": self.status,
                "error_class": self.error_class, "message": self.message, "row_count": self.row_count,
                "truncated": bool(self.result and self.result.truncated), "elapsed_ms": self.elapsed_ms}


class ResultStore:
    """Per-run store of executed queries, addressed by query_id (q1, q2, ...)."""

    def __init__(self) -> None:
        self._records: dict[str, QueryRecord] = {}

    def next_id(self) -> str:
        return f"q{len(self._records) + 1}"

    def add(self, record: QueryRecord) -> None:
        self._records[record.query_id] = record

    def get(self, query_id: str) -> QueryRecord | None:
        return self._records.get(query_id)

    def all(self) -> list[QueryRecord]:
        return list(self._records.values())


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex()  # equality-preserving (BIRD EX compares raw values)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def classify_db_error(dialect: str, exc: BaseException) -> tuple[str, str]:
    """Map a driver exception to (status, error_class)."""
    msg = str(exc).lower()
    if dialect == "sqlite":
        if "interrupted" in msg:
            return "TIMEOUT", "TIMEOUT"
        if "no such column" in msg:
            return "ERROR", "UNKNOWN_COLUMN"
        if "no such table" in msg:
            return "ERROR", "UNKNOWN_TABLE"
        if "ambiguous column" in msg:
            return "ERROR", "AMBIGUOUS_COLUMN"
        if "not authorized" in msg or "prohibited" in msg or "readonly" in msg or "read-only" in msg:
            return "ERROR", "POLICY_VIOLATION"
        if "syntax error" in msg:
            return "ERROR", "PARSE_ERROR"
        return "ERROR", "DB_ERROR"
    code = getattr(exc, "sqlstate", None) or getattr(getattr(exc, "orig", None), "sqlstate", None)
    mapping = {"57014": ("TIMEOUT", "TIMEOUT"), "42703": ("ERROR", "UNKNOWN_COLUMN"),
               "42P01": ("ERROR", "UNKNOWN_TABLE"), "42702": ("ERROR", "AMBIGUOUS_COLUMN"),
               "42501": ("ERROR", "POLICY_VIOLATION"), "25006": ("ERROR", "POLICY_VIOLATION"),
               "42601": ("ERROR", "PARSE_ERROR"), "42804": ("ERROR", "TYPE_ERROR"), "42883": ("ERROR", "TYPE_ERROR"),
               "22P02": ("ERROR", "TYPE_ERROR")}
    return mapping.get(code or "", ("ERROR", "DB_ERROR"))


class Executor:
    """Validate, then execute one statement in a read-only transaction that is always rolled back."""

    def __init__(self, db: Database, *, statement_timeout_s: float, max_rows: int, validate: bool = True,
                 fault_hook=None) -> None:  # noqa: ANN001 - optional callable(sql) raising to inject faults (E3)
        self.db = db
        self.policy = db.policy()
        self.timeout = statement_timeout_s
        self.max_rows = max_rows
        self.validate_enabled = validate
        self.fault_hook = fault_hook

    def validate(self, sql: str) -> ValidationResult:
        return validate_sql(sql, self.policy)

    def run(self, sql: str, store: ResultStore, label: str | None = None) -> QueryRecord:
        qid = store.next_id()
        validation = self.validate(sql) if self.validate_enabled else None
        if validation is not None and not validation.ok:
            rec = QueryRecord(qid, sql, label, "REJECTED", validation.error_class, validation.message(),
                              validation.hint(), validation=validation.to_dict())
            store.add(rec)
            return rec
        start = time.monotonic()
        try:
            if self.fault_hook is not None:
                self.fault_hook(sql)
            result = self._execute(sql)
            rec = QueryRecord(qid, sql, label, "OK", result=result,
                              validation=validation.to_dict() if validation else None)
        except Exception as exc:  # noqa: BLE001 - every driver error becomes a structured observation
            status, cls = classify_db_error(self.db.dialect, exc)
            message = str(getattr(exc, "orig", exc)).strip().splitlines()[0][:500] if str(exc) else type(exc).__name__
            rec = QueryRecord(qid, sql, label, status, cls, message,
                              validation=validation.to_dict() if validation else None)
        rec.elapsed_ms = int((time.monotonic() - start) * 1000)
        store.add(rec)
        return rec

    def _execute(self, sql: str) -> QueryResult:
        raw = self.db.agent_engine.raw_connection()
        try:
            dbapi = raw.driver_connection
            if self.db.dialect == "sqlite":
                return self._execute_sqlite(dbapi, sql)
            return self._execute_postgres(dbapi, sql)
        finally:
            raw.close()

    def _fetch(self, cursor) -> QueryResult:  # noqa: ANN001
        desc = cursor.description or []
        columns = [d[0] for d in desc]
        types = [str(d[1]) if d[1] is not None else "" for d in desc]
        rows = cursor.fetchmany(self.max_rows + 1)
        truncated = len(rows) > self.max_rows
        rows = [tuple(_jsonable(v) for v in r) for r in rows[: self.max_rows]]
        return QueryResult(columns, types, rows, truncated)

    def _execute_sqlite(self, conn, sql: str) -> QueryResult:  # noqa: ANN001
        guard = self.db.guard
        assert guard is not None
        with guard.lock:
            guard.deadline = time.monotonic() + self.timeout
            try:
                conn.execute("BEGIN")  # deferred read transaction; rolled back below
                cur = conn.execute(sql)
                return self._fetch(cur)
            finally:
                guard.deadline = None
                try:
                    conn.execute("ROLLBACK")
                except Exception:  # noqa: BLE001
                    pass

    def _execute_postgres(self, conn, sql: str) -> QueryResult:  # noqa: ANN001
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute(f"SET LOCAL statement_timeout = {int(self.timeout * 1000)}")
                # prepared (extended) protocol: the server itself rejects a second statement
                cur.execute(sql, prepare=True)
                result = self._fetch(cur)
                if cur.description:
                    result.column_types = [_pg_type_name(d.type_code) for d in cur.description]
                return result
        finally:
            conn.rollback()


_PG_TYPES = {16: "BOOLEAN", 20: "BIGINT", 21: "SMALLINT", 23: "INTEGER", 25: "TEXT", 700: "REAL", 701: "DOUBLE",
             1043: "VARCHAR", 1082: "DATE", 1114: "TIMESTAMP", 1184: "TIMESTAMPTZ", 1700: "NUMERIC"}


def _pg_type_name(oid) -> str:  # noqa: ANN001
    return _PG_TYPES.get(int(oid), str(oid))
