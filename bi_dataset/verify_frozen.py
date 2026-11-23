"""Freeze / verify BI test artifacts (docs/spec/BENCHMARK.md section 3.4).

    python3 -m bi_dataset.verify_frozen --instance test --freeze    # once, in Phase 2
    python3 -m bi_dataset.verify_frozen --instance test             # gate bi-test-frozen
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parent
REPO = PKG.parent
FROZEN = ["bi_dataset/events/test.json", "bi_dataset/tasks/test.jsonl", "bi_dataset/tasks/study.jsonl"]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest_path(instance: str) -> Path:
    return REPO / "data" / "manifests" / f"bi_{instance}.json"


def current(instance: str) -> dict:
    gen = json.loads((REPO / "data" / "generated" / f"retailco_{instance}" / "manifest.json").read_text())
    return {"frozen_files": {f: _sha(REPO / f) for f in FROZEN}, "csv_sha256": gen["csv_sha256"],
            "generator_sha256": gen["generator_sha256"], "events_sha256": gen["events_sha256"]}


def frozen_task_hash(split: str) -> str | None:
    p = manifest_path("test")
    if not p.exists():
        return None
    return json.loads(p.read_text()).get("frozen", {}).get("frozen_files", {}).get(f"bi_dataset/tasks/{split}.jsonl")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", default="test")
    ap.add_argument("--freeze", action="store_true")
    args = ap.parse_args(argv)
    path = manifest_path(args.instance)
    data = json.loads(path.read_text()) if path.exists() else {}
    obs = current(args.instance)
    if args.freeze:
        if "frozen" in data:
            print("already frozen; refusing to re-freeze")
            return 1
        data["frozen"] = obs
        path.write_text(json.dumps(data, indent=2) + "\n")
        print("frozen:", list(obs["frozen_files"]))
        return 0
    if "frozen" not in data:
        print("ERROR: BI test artifacts not frozen")
        return 1
    diffs = [k for k in ("frozen_files", "csv_sha256", "events_sha256") if data["frozen"][k] != obs[k]]
    if diffs:
        print("ERROR: frozen BI test artifacts changed:", diffs)
        return 1
    print("BI test artifacts frozen and unchanged:", ", ".join(data["frozen"]["frozen_files"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
