#!/usr/bin/env python3
"""Database-disjoint BIRD splits (docs/spec/BENCHMARK.md section 3.1).

    python3 scripts/make_bird_splits.py --write          # create data/splits/bird.json (once; then frozen)
    python3 scripts/make_bird_splits.py --verify-frozen  # recompute and compare with the frozen file
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV_JSON = ROOT / "data" / "downloads" / "bird" / "dev" / "dev.json"
OUT = ROOT / "data" / "splits" / "bird.json"
SEED = 20261123
TARGETS = {"test": 150, "val": 100, "dev": 100}
N_DBS = {"test": 5, "val": 3, "dev": 3}
LEVELS = ("simple", "moderate", "challenging")


def stratified(pool: list[dict], n: int, rng: random.Random) -> list[int]:
    by = {lv: sorted((r for r in pool if r["difficulty"] == lv), key=lambda r: r["question_id"]) for lv in LEVELS}
    total = len(pool)
    quotas = {lv: round(n * len(by[lv]) / total) for lv in LEVELS}
    diff = n - sum(quotas.values())
    quotas[max(LEVELS, key=lambda lv: len(by[lv]))] += diff
    chosen = []
    for lv in LEVELS:
        chosen += [r["question_id"] for r in rng.sample(by[lv], quotas[lv])]
    return sorted(chosen)


def build() -> dict:
    dev = json.loads(DEV_JSON.read_text())
    dbs = sorted({r["db_id"] for r in dev})
    seed = SEED
    while True:
        rng = random.Random(seed)
        order = dbs[:]
        rng.shuffle(order)
        assign = {"test": order[:5], "val": order[5:8], "dev": order[8:11]}
        pools = {s: [r for r in dev if r["db_id"] in assign[s]] for s in assign}
        ok = all(len(pools[s]) >= 1.5 * TARGETS[s] and all(any(r["difficulty"] == lv for r in pools[s]) for lv in LEVELS)
                 for s in assign)
        if ok:
            break
        seed += 1
    sample_rng = random.Random(seed * 7919)
    splits = {}
    for s in ("test", "val", "dev"):
        ids = stratified(pools[s], TARGETS[s], sample_rng)
        splits[s] = {"databases": sorted(assign[s]), "question_ids": ids, "pool_size": len(pools[s])}
    return {"seed": seed, "base_seed": SEED, "source_dev_json_sha256": hashlib.sha256(DEV_JSON.read_bytes()).hexdigest(),
            "targets": TARGETS, "splits": splits}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--verify-frozen", action="store_true")
    args = ap.parse_args(argv)
    if not DEV_JSON.exists():
        print("BIRD dev not downloaded; see scripts/verify_bird_manifest.py")
        return 1
    data = build()
    if args.write:
        if OUT.exists():
            print(f"refusing to overwrite frozen split {OUT.relative_to(ROOT)}")
            return 1
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(data, indent=2) + "\n")
        print("wrote", OUT.relative_to(ROOT))
        return 0
    if not OUT.exists():
        print("frozen split missing")
        return 1
    frozen = json.loads(OUT.read_text())
    if frozen != data:
        print("ERROR: recomputed split differs from frozen data/splits/bird.json")
        return 1
    dbsets = [set(v["databases"]) for v in frozen["splits"].values()]
    if any(a & b for i, a in enumerate(dbsets) for b in dbsets[i + 1:]):
        print("ERROR: splits share databases")
        return 1
    print("BIRD splits frozen and reproducible:", {k: len(v["question_ids"]) for k, v in frozen["splits"].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
