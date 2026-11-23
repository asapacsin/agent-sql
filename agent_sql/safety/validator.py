"""Deterministic SQL safety validator: rules SR1-SR12 (docs/spec/TOOLS.md section 4).

The validator is the single owner of statement admission. Every system calls it before execution;
the read-only database layer (L2/L4) remains the authoritative enforcement.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field
from typing import Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, OptimizeError, ParseError, TokenError
from sqlglot.optimizer.qualify import qualify

from agent_sql.safety.policy import (
    ALLOWED_ANONYMOUS_FUNCTIONS,
    MAX_RANDOMBLOB_BYTES,
    AccessPolicy,
    is_denied_function,
    is_system_catalog,
)

ErrorClass = Literal["PARSE_ERROR", "POLICY_VIOLATION", "UNKNOWN_TABLE", "UNKNOWN_COLUMN", "AMBIGUOUS_COLUMN",
                     "RESTRICTED_COLUMN"]

_FORBIDDEN_NODE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "TruncateTable", "Command",
    "Grant", "Revoke", "Transaction", "Commit", "Rollback", "Set", "Pragma", "Attach", "Detach", "Copy", "Into",
    "Lock", "Use", "Analyze", "Kill", "LoadData", "Refresh", "Cache", "Uncache", "Describe", "Show", "Execute",
    "Declare", "Summarize", "Comprehension",
)
FORBIDDEN_NODES = tuple(t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NODE_NAMES) if isinstance(t, type))
_SET_OPS = tuple(t for t in (getattr(exp, n, None) for n in ("Union", "Intersect", "Except", "SetOperation"))
                 if isinstance(t, type))
SQLGLOT_DIALECT = {"sqlite": "sqlite", "postgres": "postgres", "postgresql": "postgres"}


@dataclass
class Violation:
    rule: str
    detail: str
    error_class: ErrorClass = "POLICY_VIOLATION"
    hint: str | None = None


@dataclass
class ValidationResult:
    status: Literal["OK", "REJECTED"]
    violations: list[Violation] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    referenced_tables: list[str] = field(default_factory=list)
    referenced_columns: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "OK"

    @property
    def error_class(self) -> ErrorClass | None:
        return self.violations[0].error_class if self.violations else None

    def message(self) -> str:
        return "; ".join(f"{v.rule}: {v.detail}" for v in self.violations)

    def hint(self) -> str | None:
        return next((v.hint for v in self.violations if v.hint), None)

    def to_dict(self) -> dict:
        return {"status": self.status,
                "rule_violations": [{"rule": v.rule, "detail": v.detail} for v in self.violations],
                "warnings": self.warnings, "referenced_tables": self.referenced_tables,
                "referenced_columns": self.referenced_columns}


def _reject(rule: str, detail: str, cls: ErrorClass = "POLICY_VIOLATION", hint: str | None = None,
            tables: list[str] | None = None) -> ValidationResult:
    return ValidationResult("REJECTED", [Violation(rule, detail, cls, hint)], referenced_tables=tables or [])


def _depth(node: exp.Expression) -> int:
    best, stack = 0, [(node, 1)]
    while stack:
        cur, d = stack.pop()
        best = max(best, d)
        for child in cur.iter_expressions():
            stack.append((child, d + 1))
    return best


def _close(name: str, options: list[str]) -> str | None:
    matches = difflib.get_close_matches(name.lower(), options, n=3, cutoff=0.5)
    return ("did you mean: " + ", ".join(matches)) if matches else None


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name)
    return node.sql_name() if hasattr(node, "sql_name") else node.key


def validate_sql(sql: str, policy: AccessPolicy) -> ValidationResult:
    dialect = SQLGLOT_DIALECT.get(policy.dialect, policy.dialect)

    # SR10 (length) before parsing so huge inputs never reach the parser.
    if len(sql) > policy.max_sql_chars:
        return _reject("SR10", f"SQL length {len(sql)} exceeds {policy.max_sql_chars} characters")
    stripped = sql.lstrip()
    # SR11 meta-commands
    if stripped.startswith("\\") or "\n\\" in sql:
        return _reject("SR11", "backslash meta-commands are not allowed")

    # SR1 parse
    try:
        statements = sqlglot.parse(sql, read=dialect, error_level=ErrorLevel.RAISE)
    except (ParseError, TokenError) as e:
        return _reject("SR1", f"could not parse SQL: {str(e).splitlines()[0][:300]}", "PARSE_ERROR")
    statements = [s for s in statements if s is not None]
    if not statements:
        return _reject("SR1", "empty SQL", "PARSE_ERROR")
    # SR2 single statement
    if len(statements) > 1:
        return _reject("SR2", f"exactly one statement allowed, found {len(statements)}")
    tree = statements[0]

    # SR3 root type
    root_ok = isinstance(tree, exp.Select) or (_SET_OPS and isinstance(tree, _SET_OPS))
    if not root_ok:
        return _reject("SR3", f"statement type {tree.key.upper()} is not a read-only SELECT")

    # SR4 forbidden nodes anywhere
    for node in tree.walk():
        if FORBIDDEN_NODES and isinstance(node, FORBIDDEN_NODES):
            return _reject("SR4", f"forbidden operation {node.key.upper()} inside the query")
        if isinstance(node, exp.Select) and node.args.get("locks"):
            return _reject("SR4", "row locking clauses (FOR UPDATE/SHARE) are not allowed")
        if isinstance(node, exp.Select) and node.args.get("into"):
            return _reject("SR4", "SELECT ... INTO is not allowed")
    # DML inside CTEs (e.g. WITH x AS (DELETE ... RETURNING *)) parse as CTE.this of a non-select type
    for cte in tree.find_all(exp.CTE):
        if not isinstance(cte.this, (exp.Select, *(_SET_OPS or ()))):
            return _reject("SR4", f"CTE {cte.alias!r} is not a SELECT")

    # SR11 placeholders / parameters
    for node in tree.walk():
        if isinstance(node, (exp.Placeholder, exp.Parameter)):
            return _reject("SR11", "parameters/placeholders are not allowed")

    # SR10 depth and joins
    if _depth(tree) > policy.max_depth:
        return _reject("SR10", f"parse-tree depth exceeds {policy.max_depth}")
    joins = list(tree.find_all(exp.Join))
    if len(joins) > policy.max_joins:
        return _reject("SR10", f"{len(joins)} joins exceed the limit of {policy.max_joins}")

    # SR5 functions
    for fn in tree.find_all(exp.Func):
        name = _function_name(fn).lower()
        if is_denied_function(name):
            return _reject("SR5", f"function {name}() is not allowed")
        if isinstance(fn, exp.Anonymous) and name not in ALLOWED_ANONYMOUS_FUNCTIONS:
            return _reject("SR5", f"unknown function {name}() is not on the allowlist")
        if name == "randomblob":
            arg = fn.expressions[0] if fn.expressions else None
            if not isinstance(arg, exp.Literal) or not arg.is_int or int(arg.this) > MAX_RANDOMBLOB_BYTES:
                return _reject("SR5", "randomblob() argument must be a literal <= 1 MB")

    # SR12 recursive CTE must be bounded by a LIMIT
    with_ = tree.find(exp.With)
    if with_ is not None and with_.args.get("recursive") and not tree.args.get("limit"):
        return _reject("SR12", "recursive CTE requires a LIMIT on the outer query")

    # SR6 / SR7 tables
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables: list[str] = []
    for t in tree.find_all(exp.Table):
        name = t.name.lower() if t.name else ""
        if not name:
            if isinstance(t.this, exp.Func):  # table-valued function, e.g. generate_series()
                continue
            return _reject("SR6", "unsupported table expression")
        schema = t.db.lower() if t.db else None
        if t.catalog:
            return _reject("SR6", "catalog-qualified table references are not allowed")
        if is_system_catalog(name, schema):
            return _reject("SR7", f"system catalog {schema + '.' if schema else ''}{name} is not accessible")
        if name in cte_names and schema is None:
            continue
        if schema is not None and schema != (policy.schema or "").lower():
            return _reject("SR6", f"schema {schema!r} is not accessible", "UNKNOWN_TABLE")
        if name not in policy.tables:
            return _reject("SR6", f"table {name!r} does not exist or is not accessible", "UNKNOWN_TABLE",
                           _close(name, sorted(policy.tables)))
        if name not in tables:
            tables.append(name)

    # SR8 restricted columns: name-based over referenced tables (conservative), plus stars.
    restricted_here = {c for t in tables for c in policy.restricted_columns(t)}
    if restricted_here:
        for star in tree.find_all(exp.Star):
            parent = star.parent
            if isinstance(parent, exp.Count):
                continue
            if isinstance(parent, exp.Column) and parent.table and parent.table.lower() not in \
                    {t for t in tables if policy.restricted_columns(t)} | _aliases_of_restricted(tree, policy):
                continue
            return _reject("SR8", "SELECT * over a table with restricted columns; list the columns explicitly",
                           "RESTRICTED_COLUMN", "restricted: " + ", ".join(sorted(restricted_here)))
        for col in tree.find_all(exp.Column):
            if col.name and col.name.lower() in restricted_here:
                return _reject("SR8", f"column {col.name!r} is restricted", "RESTRICTED_COLUMN")

    # SR9 column existence (qualify against the catalog)
    columns: list[str] = []
    schema_map = {t: dict(policy.tables[t]) for t in tables}
    if schema_map:
        try:
            qualified = qualify(tree.copy(), schema=schema_map, dialect=dialect, validate_qualify_columns=True,
                                quote_identifiers=False, identify=False)
            for col in qualified.find_all(exp.Column):
                if col.table and col.name:
                    ref = f"{col.table}.{col.name}".lower()
                    if ref not in columns:
                        columns.append(ref)
        except OptimizeError as e:
            msg = str(e)
            all_cols = sorted({c for t in tables for c in policy.tables[t]})
            if "ambiguous" in msg.lower():
                return _reject("SR9", msg[:300], "AMBIGUOUS_COLUMN", tables=tables)
            bad = _unknown_column_name(msg)
            return _reject("SR9", msg[:300], "UNKNOWN_COLUMN", _close(bad, all_cols) if bad else None, tables)
        except (ValueError, KeyError, NotImplementedError) as e:  # qualifier limitation: L2/L4 still enforce
            warnings = [{"code": "QUALIFY_SKIPPED", "detail": f"column check skipped: {type(e).__name__}"}]
            return ValidationResult("OK", [], warnings + _warnings(tree, policy, tables), tables, columns)

    return ValidationResult("OK", [], _warnings(tree, policy, tables), tables, columns)


def _aliases_of_restricted(tree: exp.Expression, policy: AccessPolicy) -> set[str]:
    return {t.alias.lower() for t in tree.find_all(exp.Table) if t.alias and policy.restricted_columns(t.name)}


def _unknown_column_name(message: str) -> str | None:
    # sqlglot messages look like: "Column 'foo' could not be resolved..."
    if "'" in message:
        parts = message.split("'")
        if len(parts) >= 2:
            return parts[1].split(".")[-1]
    return None


def _warnings(tree: exp.Expression, policy: AccessPolicy, tables: list[str]) -> list[dict]:
    out = []
    for join in tree.find_all(exp.Join):
        # sqlglot's SQLite dialect parses comma joins as CROSS, so every unconditioned join warns (non-blocking)
        if not join.args.get("on") and not join.args.get("using"):
            out.append({"code": "CARTESIAN_JOIN", "detail": "join without a join condition"})
            break
    return out
