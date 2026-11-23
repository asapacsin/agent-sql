"""Deterministic verification checks V1-V8 (docs/spec/BASELINES.md section A2).

Each check returns a ValidationCheck (PASS / WARN / FAIL / N/A). The same functions run in-loop for A2 and post hoc
for every system in the evaluator (M11), so the metric is comparable across systems.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import sqlglot
from sqlglot import exp

from agent_sql.systems.contracts import FinalAnswer, ValidationCheck, ValidationReport
from agent_sql.verification.grounding import check_grounding
from agent_sql.verification.metrics_registry import load_registry
from agent_sql.verification.numeric import close, to_number

REL = 0.005


@dataclass
class QueryView:
    """What the checks need from a query: SQL text and (for OK queries) columns + rows."""
    query_id: str
    sql: str
    status: str
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    truncated: bool = False


@dataclass
class CheckInput:
    answer: FinalAnswer
    queries: dict[str, QueryView]
    dialect: str
    is_bi: bool
    periods: list[dict] = field(default_factory=list)  # [{label, start, end}] from the plan (A2) or empty
    metric_ids: list[str] = field(default_factory=list)
    comparisons: list[dict] = field(default_factory=list)  # compare_query_results observations
    canonical_totals: dict[tuple[str, str, str], float] = field(default_factory=dict)  # (metric, start, end) -> total


def _parse(sql: str, dialect: str):  # noqa: ANN202
    try:
        return sqlglot.parse_one(sql, read="postgres" if dialect == "postgres" else "sqlite")
    except Exception:  # noqa: BLE001
        return None


def _final(ci: CheckInput) -> QueryView | None:
    q = ci.queries.get(ci.answer.final_query_id or "")
    return q if q and q.status == "OK" else None


# ---------------------------------------------------------------- V1
def v1(ci: CheckInput) -> ValidationCheck:
    def lookup(qid: str, row: int, col: str):  # noqa: ANN202
        q = ci.queries.get(qid)
        if not q or q.status != "OK" or col not in q.columns or not 0 <= row < len(q.rows):
            return False, None
        return True, q.rows[row][q.columns.index(col)]

    return check_grounding(ci.answer.claims, lookup)[0]


# ---------------------------------------------------------------- V2 metric conformance
def _has_recognized_status(tree) -> bool:  # noqa: ANN001
    for node in tree.find_all(exp.In):
        col = node.this
        if isinstance(col, exp.Column) and col.name.lower() == "status":
            vals = {e.name.lower() for e in node.expressions if isinstance(e, exp.Literal)}
            if vals == {"shipped", "completed"}:
                return True
    return False


def _excludes_test_accounts(tree) -> bool:  # noqa: ANN001
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.Not, exp.Is):
        cols = [c for c in node.find_all(exp.Column) if c.name.lower() == "is_test_account"]
        if not cols:
            continue
        if isinstance(node, exp.Not):
            return True
        rhs = node.expression
        lit = rhs.sql().lower() if rhs is not None else ""
        if isinstance(node, exp.EQ) and lit in ("false", "0"):
            return True
        if isinstance(node, exp.NEQ) and lit in ("true", "1"):
            return True
        if isinstance(node, exp.Is) and lit in ("false",):
            return True
    return False


def v2(ci: CheckInput) -> ValidationCheck:
    if not ci.is_bi:
        return ValidationCheck(id="V2", result="N/A", detail="no metric registry for this database")
    reg = load_registry()
    metric_ids = [m for m in dict.fromkeys(ci.metric_ids + [m.metric_id for m in ci.answer.metric_interpretations])
                  if m in reg]
    needs = [m for m in metric_ids if reg.get(m).required_predicates]
    final = _final(ci)
    if not needs or final is None:
        return ValidationCheck(id="V2", result="N/A", detail="no registry metric with required predicates")
    tree = _parse(final.sql, ci.dialect)
    if tree is None:
        return ValidationCheck(id="V2", result="FAIL", detail="final SQL not parseable")
    missing = []
    if not _has_recognized_status(tree):
        missing.append("orders.status IN ('shipped','completed')")
    if not _excludes_test_accounts(tree):
        missing.append("customers.is_test_account = FALSE")
    if missing:
        return ValidationCheck(id="V2", result="FAIL", detail=f"{needs}: missing {missing}")
    return ValidationCheck(id="V2", result="PASS", detail=f"required predicates present for {needs}")


# ---------------------------------------------------------------- V3 reconciliation
def v3(ci: CheckInput) -> ValidationCheck:
    final = _final(ci)
    if not ci.is_bi or final is None or len(final.rows) < 2 or not ci.canonical_totals:
        return ValidationCheck(id="V3", result="N/A", detail="no breakdown with a canonical total")
    numeric = [i for i, _ in enumerate(final.columns)
               if all(to_number(r[i]) is not None for r in final.rows if r[i] is not None)
               and any(r[i] is not None for r in final.rows)]
    keyish = [i for i in range(len(final.columns)) if i not in numeric]
    if not numeric or not keyish:
        return ValidationCheck(id="V3", result="N/A", detail="result is not a breakdown")
    sums = {final.columns[i]: sum(to_number(r[i]) or 0.0 for r in final.rows) for i in numeric}
    for (metric, start, end), total in ci.canonical_totals.items():
        for col, s in sums.items():
            if close(s, total, REL, 0.01):
                return ValidationCheck(id="V3", result="PASS",
                                       detail=f"sum({col})={s:.2f} reconciles with {metric} {start}..{end}")
    if len(numeric) == 1:
        (col, s), = sums.items()
        (metric, start, end), total = next(iter(ci.canonical_totals.items()))
        return ValidationCheck(id="V3", result="FAIL",
                               detail=f"sum({col})={s:.2f} != canonical {metric} {start}..{end} = {total:.2f}")
    return ValidationCheck(id="V3", result="WARN", detail="no numeric column reconciles with a canonical total")


# ---------------------------------------------------------------- V4 period conformance
def v4(ci: CheckInput) -> ValidationCheck:
    if not ci.is_bi or not ci.periods:
        return ValidationCheck(id="V4", result="N/A", detail="no plan periods")
    sqls = " ".join(q.sql for q in ci.queries.values() if q.status == "OK")
    missing, closed = [], []
    for p in ci.periods:
        start, end = p.get("start"), p.get("end")
        if start and start not in sqls:
            missing.append(f"{p.get('label')}: start {start}")
        if end and end not in sqls:
            try:
                prev = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
            except ValueError:
                prev = ""
            (closed if prev and prev in sqls else missing).append(f"{p.get('label')}: end {end}")
    if missing:
        return ValidationCheck(id="V4", result="FAIL", detail=f"period bounds not used: {missing}")
    if closed:
        return ValidationCheck(id="V4", result="WARN", detail=f"closed-interval end used: {closed}")
    return ValidationCheck(id="V4", result="PASS", detail="plan periods used in SQL")


# ---------------------------------------------------------------- V5 fan-out
def v5(ci: CheckInput) -> ValidationCheck:
    final = _final(ci)
    if final is None or not final.rows:
        return ValidationCheck(id="V5", result="N/A", detail="no final result")
    keys = [i for i in range(len(final.columns))
            if not all(to_number(r[i]) is not None for r in final.rows if r[i] is not None)]
    if not keys or len(final.rows) == 1:
        return ValidationCheck(id="V5", result="N/A", detail="no grain key")
    grain = [tuple(r[i] for i in keys) for r in final.rows]
    dups = len(grain) - len(set(grain))
    if dups:
        return ValidationCheck(id="V5", result="FAIL", detail=f"{dups} duplicate grain keys (possible join fan-out)")
    return ValidationCheck(id="V5", result="PASS", detail="grain key unique")


# ---------------------------------------------------------------- V6, V7
def v6(ci: CheckInput) -> ValidationCheck:
    if ci.answer.status != "ANSWERED":
        return ValidationCheck(id="V6", result="N/A", detail="not answered")
    final = _final(ci)
    if final is None or not final.rows:
        return ValidationCheck(id="V6", result="FAIL", detail="final result empty")
    for i, c in enumerate(final.columns):
        nulls = sum(1 for r in final.rows if r[i] is None)
        if nulls / len(final.rows) > 0.5:
            return ValidationCheck(id="V6", result="WARN", detail=f"column {c} is >50% null")
    return ValidationCheck(id="V6", result="PASS", detail="")


def v7(ci: CheckInput) -> ValidationCheck:
    final = _final(ci)
    if final is None:
        return ValidationCheck(id="V7", result="N/A", detail="no final result")
    if not final.truncated:
        return ValidationCheck(id="V7", result="PASS", detail="")
    disclosed = any("trunc" in x.lower() for x in ci.answer.limitations)
    return ValidationCheck(id="V7", result="WARN" if disclosed else "FAIL",
                           detail="result truncated" + (" (disclosed)" if disclosed else " and not disclosed"))


# ---------------------------------------------------------------- V8 cross-query consistency
def v8(ci: CheckInput) -> ValidationCheck:
    if not ci.comparisons:
        return ValidationCheck(id="V8", result="N/A", detail="no cross-query comparison performed")
    bad = [c for c in ci.comparisons if c.get("mismatched") or c.get("within_tolerance") is False]
    if bad:
        return ValidationCheck(id="V8", result="FAIL", detail=f"{len(bad)} comparison(s) disagree")
    return ValidationCheck(id="V8", result="PASS", detail=f"{len(ci.comparisons)} comparison(s) agree")


CHECKS = (v1, v2, v3, v4, v5, v6, v7, v8)


def run_checks(ci: CheckInput) -> ValidationReport:
    checks = [fn(ci) for fn in CHECKS]
    status = ("FAILED" if any(c.result == "FAIL" for c in checks)
              else "WARNINGS" if any(c.result == "WARN" for c in checks) else "PASSED")
    return ValidationReport(status=status, checks=checks)


def canonical_totals(metric_ids: list[str], periods: list[dict], run_template) -> dict:  # noqa: ANN001
    """Totals for registry metrics with canonical templates over plan periods; run_template(sql)->float|None."""
    reg = load_registry()
    out = {}
    for m in metric_ids:
        if m not in reg or not reg.get(m).template:
            continue
        for p in periods:
            try:
                sql = reg.get(m).render_template(p["start"], p["end"])
            except (KeyError, ValueError):
                continue
            v = run_template(sql)
            if v is not None:
                out[(m, p["start"], p["end"])] = float(v)
    return out
