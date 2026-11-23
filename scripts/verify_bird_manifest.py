#!/usr/bin/env python3
"""Verify (or create with --write) data/manifests/bird_dev.json against the downloaded BIRD dev files.

Expected per docs/spec/BENCHMARK.md section 1: 1,534 questions over 11 databases.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIRD = ROOT / "data" / "downloads" / "bird"
MANIFEST = ROOT / "data" / "manifests" / "bird_dev.json"
URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def observe() -> dict:
    dev = json.loads((BIRD / "dev" / "dev.json").read_text())
    dbs = sorted({r["db_id"] for r in dev})
    return {"source_url": URL, "archive_sha256": sha(BIRD / "dev.zip") if (BIRD / "dev.zip").exists() else None,
            "dev_json_sha256": sha(BIRD / "dev" / "dev.json"), "n_questions": len(dev), "databases": dbs,
            "questions_per_db": dict(sorted(collections.Counter(r["db_id"] for r in dev).items())),
            "difficulty": dict(sorted(collections.Counter(r["difficulty"] for r in dev).items())),
            "db_sqlite_sha256": {d: sha(BIRD / "dev" / "dev_databases" / d / f"{d}.sqlite") for d in dbs}}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    if not (BIRD / "dev" / "dev.json").exists():
        print(f"BIRD dev not downloaded. Download {URL} into {BIRD}/dev.zip and extract dev_20240627 as dev/")
        return 1
    obs = observe()
    errors = []
    if obs["n_questions"] != 1534 or len(obs["databases"]) != 11:
        errors.append(f"unexpected counts: {obs['n_questions']} questions, {len(obs['databases'])} databases")
    if args.write:
        MANIFEST.write_text(json.dumps(obs, indent=2) + "\n")
        print("wrote", MANIFEST.relative_to(ROOT))
    elif not MANIFEST.exists():
        errors.append("manifest missing (run with --write once)")
    else:
        recorded = json.loads(MANIFEST.read_text())
        for key in ("dev_json_sha256", "n_questions", "databases", "db_sqlite_sha256"):
            if recorded.get(key) != obs.get(key):
                errors.append(f"manifest mismatch on {key}")
    for e in errors:
        print("ERROR", e)
    if not errors:
        print(f"BIRD manifest OK: {obs['n_questions']} questions, {len(obs['databases'])} databases")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
