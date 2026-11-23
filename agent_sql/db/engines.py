"""Read-only database engines (docs/spec/ARCHITECTURE.md section 7; TOOLS.md section 4.1 layers L2-L4).

Two connections per database:
  * agent engine   - executes validated agent SQL. SQLite: mode=ro URI, query_only, authorizer, progress
                     handler timeout. PostgreSQL: `agent_ro` role (SELECT-only, column grants, read-only default).
  * catalog engine - schema introspection only; never runs agent-written SQL.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from agent_sql.safety.policy import is_denied_function


@dataclass
class SqliteGuard:
    """Per-connection state for the SQLite authorizer and timeout progress handler."""
    restricted: dict[str, set[str]] = field(default_factory=dict)
    deadline: float | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def authorizer(self, action, arg1, arg2, dbname, source):  # noqa: ANN001 - sqlite3 callback signature
        if action == sqlite3.SQLITE_SELECT or action == sqlite3.SQLITE_RECURSIVE:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            table, column = (arg1 or "").lower(), (arg2 or "").lower()
            if table.startswith("sqlite_") and table not in {"sqlite_sequence"}:
                return sqlite3.SQLITE_DENY
            if column in self.restricted.get(table, set()):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_TRANSACTION and (arg1 or "").upper() in {"BEGIN", "ROLLBACK"}:
            return sqlite3.SQLITE_OK  # executor's own read transaction wrapper
        if action == sqlite3.SQLITE_PRAGMA and (arg1 or "").lower() == "read_uncommitted" and arg2 is None:
            return sqlite3.SQLITE_OK  # SQLAlchemy reads isolation level on connect; read form only
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if is_denied_function(arg2 or "") else sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def progress(self) -> int:
        if self.deadline is not None and time.monotonic() > self.deadline:
            return 1  # non-zero interrupts the statement
        return 0


def _sqlite_connect(path: Path, guard: SqliteGuard | None) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA query_only = ON")
    if guard is not None:
        conn.set_authorizer(guard.authorizer)
        conn.set_progress_handler(guard.progress, 1000)
    return conn


def sqlite_engines(path: Path, restricted: dict[str, set[str]] | None = None) -> tuple[Engine, Engine, SqliteGuard]:
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    guard = SqliteGuard(restricted={k.lower(): {c.lower() for c in v} for k, v in (restricted or {}).items()})
    agent = create_engine("sqlite://", creator=lambda: _sqlite_connect(Path(path), guard), poolclass=NullPool)
    catalog = create_engine("sqlite://", creator=lambda: _sqlite_connect(Path(path), None), poolclass=NullPool)
    return agent, catalog, guard


def postgres_engines(agent_url: str, catalog_url: str | None = None) -> tuple[Engine, Engine]:
    """agent_url must authenticate as the read-only role; catalog_url defaults to the same role."""
    agent = create_engine(agent_url, poolclass=NullPool)
    catalog = create_engine(catalog_url or agent_url, poolclass=NullPool)
    return agent, catalog
