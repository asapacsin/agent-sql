"""Result comparators (docs/spec/BENCHMARK.md section 4).

* bird_execution_match: official BIRD semantics, set(rows(pred)) == set(rows(gold)).
* score_bi_result: answer_spec scoring for scalar/table/set result types.
* score_bi_diagnostic / score_bi_insufficient: answer-level scoring.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from agent_sql.systems.contracts import FinalAnswer

DOWN_WORDS = re.compile(r"\b(declin\w*|drop\w*|decreas\w*|fell|fall\w*|lower\w*|down|reduc\w*|dip\w*|shrank|loss)\b",
                        re.IGNORECASE)
UP_WORDS = re.compile(r"\b(increas\w*|rose|rise\w*|grew|grow\w*|higher|up|gain\w*|jump\w*)\b", re.IGNORECASE)


@dataclass
class Verdict:
    passed: bool
    detail: str
    score: float | None = None  # e.g. F1 for sets
    extras: dict = field(default_factory=dict)


# ----------------------------------------------------------------------------- primitives

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


def close(pred: Any, gold: Any, rel_tol: float = 0.005, abs_tol: float = 0.01) -> bool:
    p, g = to_number(pred), to_number(gold)
    if g is None:
        if gold is None:
            return pred is None
        return norm_key(pred) == norm_key(gold)
    if p is None:
        return False
    if abs(p - g) <= max(abs_tol, rel_tol * abs(g)):
        return True
    # percentages expressed as 0-100 vs fractions
    return abs(g) <= 1.5 and abs(p / 100 - g) <= max(abs_tol / 100, rel_tol * abs(g))


def norm_key(v: Any) -> str:
    n = to_number(v)
    if n is not None and not isinstance(v, str):
        return str(int(n)) if float(n).is_integer() else repr(round(n, 6))
    if isinstance(v, str) and to_number(v) is not None and re.fullmatch(r"-?\d+(\.0+)?", v.strip()):
        return str(int(float(v)))
    return str(v).strip().lower() if v is not None else "<null>"


def bird_execution_match(pred_rows: list[tuple], gold_rows: list[tuple]) -> bool:
    """Official BIRD EX: set equality of result rows (order and duplicates ignored, column order significant)."""
    return set(map(tuple, pred_rows)) == set(map(tuple, gold_rows))


# ----------------------------------------------------------------------------- BI result types

def _col(rows: list[tuple], i: int) -> list[Any]:
    return [r[i] for r in rows]


def _map_key_column(gold_vals: list[Any], pred_rows: list[tuple], ncols: int, used: set[int]) -> int | None:
    gold = {norm_key(v) for v in gold_vals}
    best, best_hits = None, 0
    for j in range(ncols):
        if j in used:
            continue
        hits = len(gold & {norm_key(v) for v in _col(pred_rows, j)})
        if hits > best_hits:
            best, best_hits = j, hits
    return best if best_hits > 0 else None


def score_bi_result(spec: dict, pred_cols: list[str], pred_rows: list[tuple], gold_cols: list[str],
                    gold_rows: list[tuple]) -> Verdict:
    kind = spec["type"]
    if kind == "scalar":
        vc = spec["value_columns"][0]
        gold = gold_rows[0][0] if gold_rows else None
        if not pred_rows:
            return Verdict(False, "empty result")
        if len(pred_rows) > 5:
            return Verdict(False, f"scalar answer returned {len(pred_rows)} rows")
        hit = any(close(v, gold, vc["rel_tol"], vc["abs_tol"]) for v in pred_rows[0])
        return Verdict(hit, f"gold={gold} pred_first_row={list(pred_rows[0])[:6]}")
    if kind == "set":
        key = spec["key_columns"][0]
        gi = gold_cols.index(key)
        gold = {norm_key(v) for v in _col(gold_rows, gi)}
        j = _map_key_column(list(gold), pred_rows, len(pred_cols), set())
        if j is None:
            return Verdict(False, "no predicted column overlaps the gold key set", 0.0)
        pred = {norm_key(v) for v in _col(pred_rows, j)}
        tp = len(gold & pred)
        prec = tp / len(pred) if pred else 0.0
        rec = tp / len(gold) if gold else 1.0
        f1 = 0.0 if tp == 0 else 2 * prec * rec / (prec + rec)
        return Verdict(f1 >= spec.get("min_f1", 0.95), f"F1={f1:.3f} (|gold|={len(gold)}, |pred|={len(pred)})", f1)
    if kind == "table":
        return _score_table(spec, pred_cols, pred_rows, gold_cols, gold_rows)
    raise ValueError(f"not a result-checkable spec type: {kind}")


def _apply_key_transform(spec: dict, gold_cols: list[str], gold_rows: list[tuple]) -> list[tuple]:
    tf = spec.get("key_transform") or {}
    if not tf:
        return gold_rows
    out = []
    for r in gold_rows:
        r = list(r)
        for col, op in tf.items():
            i = gold_cols.index(col)
            if op == "plus_one":
                r[i] = to_number(r[i]) + 1
        out.append(tuple(r))
    return out


def _score_table(spec: dict, pred_cols: list[str], pred_rows: list[tuple], gold_cols: list[str],
                 gold_rows: list[tuple]) -> Verdict:
    gold_rows = _apply_key_transform(spec, gold_cols, gold_rows)
    if not gold_rows:
        return Verdict(not pred_rows, "gold empty")
    if not pred_rows:
        return Verdict(False, "empty result")
    keys = spec["key_columns"]
    kidx = [gold_cols.index(k) for k in keys]
    used: set[int] = set()
    kmap = []
    for gi in kidx:
        j = _map_key_column(_col(gold_rows, gi), pred_rows, len(pred_cols), used)
        if j is None:
            return Verdict(False, f"no predicted column matches gold key {gold_cols[gi]!r}")
        used.add(j)
        kmap.append(j)
    pred_index: dict[tuple, tuple] = {}
    pred_order: list[tuple] = []
    for r in pred_rows:
        k = tuple(norm_key(r[j]) for j in kmap)
        if k not in pred_index:
            pred_index[k] = r
            pred_order.append(k)
    gold_keys = [tuple(norm_key(r[i]) for i in kidx) for r in gold_rows]
    missing = [k for k in gold_keys if k not in pred_index]
    if missing:
        return Verdict(False, f"{len(missing)} gold keys missing, e.g. {missing[0]}")
    extra = len(set(pred_order) - set(gold_keys))
    if spec.get("top_k") is None and extra > max(0, int(0.05 * len(gold_keys))):
        return Verdict(False, f"{extra} extra keys beyond gold")
    # value columns: map each by tolerance match rate >= 95%
    for vc in spec["value_columns"]:
        gi = gold_cols.index(vc["name"])
        best_rate, best_j = 0.0, None
        for j in range(len(pred_cols)):
            if j in kmap:
                continue
            ok = sum(close(pred_index[k][j], g[gi], vc["rel_tol"], vc["abs_tol"]) for k, g in zip(gold_keys, gold_rows, strict=False))
            rate = ok / len(gold_keys)
            if rate > best_rate:
                best_rate, best_j = rate, j
        if best_j is None or best_rate < 0.95:
            return Verdict(False, f"value column {vc['name']!r}: best match rate {best_rate:.2f}")
    if spec.get("ordered"):
        k = spec.get("top_k") or len(gold_keys)
        head = [p for p in pred_order if p in set(gold_keys)][:k] if spec.get("top_k") is None else pred_order[:k]
        if head != gold_keys[:k]:
            return Verdict(False, f"order mismatch: pred={head} gold={gold_keys[:k]}")
    return Verdict(True, f"{len(gold_keys)} gold rows matched")


# ----------------------------------------------------------------------------- answer-level types

def _entities_match(claim_entities: list, wanted: list[dict]) -> bool:
    have = {(e.type, e.value.strip().lower()) for e in claim_entities}
    have_values = {v for _, v in have}
    for w in wanted:
        val = w["value"].strip().lower()
        if (w["type"], val) not in have and val not in have_values \
                and not any(val in v or v in val for v in have_values if len(v) > 3):
            return False
    return True


def _direction_ok(claim, direction: str | None) -> bool:  # noqa: ANN001
    if direction is None:
        return True
    words = DOWN_WORDS if direction == "down" else UP_WORDS
    if words.search(claim.text or ""):
        return True
    v = to_number(claim.value)
    return v is not None and ((v < 0) if direction == "down" else (v > 0))


def score_bi_diagnostic(spec: dict, answer: FinalAnswer, events: list[dict]) -> Verdict:
    """Required findings matched against structured claims; numeric contradiction is checked only for findings
    that declare require_value (with an evaluator-computed value in `finding['gold_value']`)."""
    if answer.status != spec.get("expected_status", "ANSWERED"):
        return Verdict(False, f"status {answer.status}")
    failed = []
    for f in spec["required_findings"]:
        if "event" in f:
            want = f["event"]
            ids = {str(e["event_id"]) for e in events if e["event_type"] == want["event_type"]
                   and (want.get("region") is None or e.get("region_name") == want["region"])}
            types = {want["event_type"].lower(), want["event_type"].replace("_", " ").lower()}
            ok = any(e.type == "event" and (e.value.strip() in ids or e.value.strip().lower() in types
                                            or any(t in e.value.lower() for t in types))
                     for c in answer.claims for e in c.entities)
        else:
            ok = any(_entities_match(c.entities, f.get("entities", [])) and _direction_ok(c, f.get("direction"))
                     and (not f.get("require_value") or close(c.value, f.get("gold_value")))
                     for c in answer.claims)
        if not ok:
            failed.append(f["id"])
    if spec.get("uncertainty_high") and answer.uncertainty.level != "high":
        failed.append("uncertainty_high")
    return Verdict(not failed, "all findings matched" if not failed else f"unmatched: {failed}")


def score_bi_insufficient(spec: dict, answer: FinalAnswer) -> Verdict:
    numeric = [c for c in answer.claims if c.is_numeric]
    if answer.status == "INSUFFICIENT_DATA":
        return Verdict(not numeric, "abstained" if not numeric else "abstained but asserted numbers")
    if answer.status == "ANSWERED" and answer.limitations and not numeric:
        return Verdict(True, "answered with limitations and no numeric claims")
    return Verdict(False, f"status {answer.status} with {len(numeric)} numeric claims")
