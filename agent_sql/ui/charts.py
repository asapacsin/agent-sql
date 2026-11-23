"""Deterministic chart selection (docs/spec/ARCHITECTURE.md section 9.2). No LLM-generated plotting code."""
from __future__ import annotations

from dataclasses import dataclass

from agent_sql.verification.numeric import to_number

TIME_NAMES = ("date", "month", "quarter", "year", "week", "period", "day")


@dataclass(frozen=True)
class ChartChoice:
    kind: str  # line | bar | table
    x: str | None = None
    y: str | None = None


def _numeric(rows: list[tuple], i: int) -> bool:
    vals = [r[i] for r in rows if r[i] is not None]
    return bool(vals) and all(to_number(v) is not None and not isinstance(v, str) for v in vals)


def choose_chart(columns: list[str], rows: list[tuple], hint: dict | None = None) -> ChartChoice:
    numeric = [i for i in range(len(columns)) if _numeric(rows, i)]
    timeish = [i for i, c in enumerate(columns) if any(t in c.lower() for t in TIME_NAMES)]
    categorical = [i for i in range(len(columns)) if i not in numeric]

    def valid(kind: str, x: str | None, y: str | None) -> bool:
        if x not in columns or y not in columns or columns.index(y) not in numeric:
            return False
        if kind == "line":
            return columns.index(x) in timeish and len(rows) >= 3
        if kind == "bar":
            return 2 <= len(rows) <= 20
        return False

    if hint and hint.get("kind") in ("line", "bar") and valid(hint["kind"], hint.get("x"), hint.get("y")):
        return ChartChoice(hint["kind"], hint["x"], hint["y"])
    y_candidates = [i for i in numeric if i not in timeish]
    if timeish and y_candidates and len(rows) >= 3:
        return ChartChoice("line", columns[timeish[0]], columns[y_candidates[0]])
    if len(categorical) == 1 and len(numeric) == 1 and 2 <= len(rows) <= 20:
        return ChartChoice("bar", columns[categorical[0]], columns[numeric[0]])
    return ChartChoice("table")
