"""Result comparators (docs/spec/BENCHMARK.md section 4).

* bird_execution_match: official BIRD semantics, set(rows(pred)) == set(rows(gold)).
* score_bi_result: answer_spec scoring for scalar/table/set result types.
* score_bi_diagnostic / score_bi_insufficient: answer-level scoring.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent_sql.systems.contracts import FinalAnswer
from agent_sql.verification.numeric import close, to_number  # noqa: F401 - re-exported

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
        hit = any(close(v, gold, vc["rel_tol"], vc["abs_tol"], vc.get("ratio", False)) for v in pred_rows[0])
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
            ok = sum(close(pred_index[k][j], g[gi], vc["rel_tol"], vc["abs_tol"], vc.get("ratio", False)) for k, g in zip(gold_keys, gold_rows, strict=False))
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

def _norm_entity(v: str) -> str:
    return re.sub(r"[\s_\-]+", " ", str(v)).strip().lower()


def _entities_match(claim_entities: list, wanted: list[dict]) -> bool:
    """Typed, exact (case/separator-insensitive) entity match: every wanted entity must be present."""
    have = {(e.type, _norm_entity(e.value)) for e in claim_entities}
    return all((w["type"], _norm_entity(w["value"])) in have for w in wanted)


def _direction_of(claim) -> str | None:  # noqa: ANN001
    down, up = bool(DOWN_WORDS.search(claim.text or "")), bool(UP_WORDS.search(claim.text or ""))
    if down != up:
        return "down" if down else "up"
    return None


def _direction_ok(claim, direction: str | None) -> bool:  # noqa: ANN001
    if direction is None:
        return True
    if _direction_of(claim) == direction:
        return True
    v = to_number(claim.value)
    return _direction_of(claim) is None and v is not None and ((v < 0) if direction == "down" else (v > 0))


def _finding_ok(f: dict, claim) -> bool:  # noqa: ANN001
    if not _entities_match(claim.entities, f.get("entities", [])):
        return False
    if not _direction_ok(claim, f.get("direction")):
        return False
    if f.get("period") and not _entities_match(claim.entities, [{"type": "period", "value": f["period"]}]):
        return False
    v = to_number(claim.value)
    if f.get("min_abs_effect") is not None and (v is None or abs(v) < float(f["min_abs_effect"])):
        return False
    return not (f.get("require_value") and not close(claim.value, f.get("gold_value")))


def _contradicts(f: dict, claim) -> bool:  # noqa: ANN001
    """A numeric claim about exactly the finding's entities that states the opposite direction, or (when the
    finding carries an evaluator-computed gold value) a value outside tolerance."""
    if not claim.is_numeric or not f.get("entities") or not _entities_match(claim.entities, f["entities"]):
        return False
    d = f.get("direction")
    stated = _direction_of(claim)
    if d and stated and stated != d:
        return True
    return bool(f.get("require_value") and f.get("gold_value") is not None and not close(claim.value, f["gold_value"]))


def score_bi_diagnostic(spec: dict, answer: FinalAnswer, events: list[dict]) -> Verdict:
    """BENCHMARK.md section 4.2 diagnostic rule: every required finding satisfied and no contradicted claim.

    Event findings name {event_type, region?}; the evaluator resolves them to business_events ids and requires an
    `event` entity EQUAL to one of those ids. Findings may add period, min_abs_effect and require_value (gold_value is
    injected by the evaluator from the task's gold SQL)."""
    if answer.status != spec.get("expected_status", "ANSWERED"):
        return Verdict(False, f"status {answer.status}")
    failed, contradicted = [], []
    for f in spec["required_findings"]:
        if "event" in f:
            want = f["event"]
            ids = {str(e["event_id"]) for e in events if e["event_type"] == want["event_type"]
                   and (want.get("region") is None or e.get("region_name") == want["region"])}
            ok = any(e.type == "event" and e.value.strip() in ids for c in answer.claims for e in c.entities)
        else:
            ok = any(_finding_ok(f, c) for c in answer.claims)
            contradicted += [c.claim_id for c in answer.claims if _contradicts(f, c)]
        if not ok:
            failed.append(f["id"])
    if spec.get("uncertainty_high") and answer.uncertainty.level != "high":
        failed.append("uncertainty_high")
    if contradicted:
        failed.append(f"contradicted:{sorted(set(contradicted))}")
    return Verdict(not failed, "all findings matched" if not failed else f"unmatched: {failed}")


def score_bi_insufficient(spec: dict, answer: FinalAnswer) -> Verdict:
    numeric = [c for c in answer.claims if c.is_numeric]
    if answer.status == "INSUFFICIENT_DATA":
        return Verdict(not numeric, "abstained" if not numeric else "abstained but asserted numbers")
    if answer.status == "ANSWERED" and answer.limitations and not numeric:
        return Verdict(True, "answered with limitations and no numeric claims")
    return Verdict(False, f"status {answer.status} with {len(numeric)} numeric claims")
