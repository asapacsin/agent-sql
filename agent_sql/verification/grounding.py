"""V1 grounding: each numeric claim must equal its evidence cell (docs/spec/BASELINES.md section A2)."""
from __future__ import annotations

from collections.abc import Callable

from agent_sql.systems.contracts import Claim, ValidationCheck

REL_TOL = 0.005


def _num(v) -> float | None:  # noqa: ANN001
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").replace("$", "").rstrip("%"))
    except ValueError:
        return None


def claim_matches_cell(claim_value, cell) -> bool:  # noqa: ANN001
    c, v = _num(claim_value), _num(cell)
    if c is None or v is None:
        return str(claim_value).strip().lower() == str(cell).strip().lower()
    if abs(c - v) <= REL_TOL * max(abs(v), 1e-12) or abs(c - v) < 0.005:
        return True
    # rounded display: claim equals the cell rounded to the claim's precision
    s = repr(c)
    decimals = len(s.split(".")[1]) if "." in s and not s.endswith(".0") else 0
    if round(v, decimals) == c:
        return True
    # percent display of a fraction
    return abs(v) <= 1.5 and abs(c / 100 - v) <= REL_TOL * max(abs(v), 1e-12) + 0.0005


CellLookup = Callable[[str, int, str], tuple[bool, object]]  # (query_id,row,col) -> (found, value)


def check_grounding(claims: list[Claim], lookup: CellLookup) -> tuple[ValidationCheck, list[str]]:
    numeric = [c for c in claims if c.is_numeric]
    if not numeric:
        return ValidationCheck(id="V1", result="N/A", detail="no numeric claims"), []
    bad = []
    for c in numeric:
        if c.evidence is None:
            bad.append(c.claim_id)
            continue
        found, cell = lookup(c.evidence.query_id, c.evidence.row_index, c.evidence.column)
        if not found or not claim_matches_cell(c.value, cell):
            bad.append(c.claim_id)
    if bad:
        return ValidationCheck(id="V1", result="FAIL", detail=f"ungrounded claims: {bad}"), bad
    return ValidationCheck(id="V1", result="PASS", detail=f"{len(numeric)} numeric claims grounded"), []
