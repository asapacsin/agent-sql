"""Numeric parsing and tolerance shared by tools, verifier and evaluator."""
from __future__ import annotations

import math
from typing import Any


def to_number(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return None if (isinstance(v, float) and math.isnan(v)) else float(v)
    if isinstance(v, str):
        s = v.strip().replace(",", "").replace("$", "")
        pct = s.endswith("%")
        s = s.rstrip("%")
        try:
            x = float(s)
        except ValueError:
            return None
        return x / 100 if pct else x
    return None


def close(pred: Any, gold: Any, rel_tol: float = 0.005, abs_tol: float = 0.01, ratio: bool = False) -> bool:
    p, g = to_number(pred), to_number(gold)
    if g is None:
        if gold is None:
            return pred is None
        return str(pred).strip().lower() == str(gold).strip().lower()
    if p is None:
        return False
    if abs(p - g) <= max(abs_tol, rel_tol * abs(g)):
        return True
    # percentages expressed as 0-100 vs fractions: only for value columns declared as ratios
    return ratio and abs(g) <= 1.5 and abs(p / 100 - g) <= max(abs_tol / 100, rel_tol * abs(g))
