#!/usr/bin/env python3
"""Run the gates of docs/spec/contracts/phase-N.json exactly as written and record commit-bound evidence.

    python3 scripts/run_gates.py --phase 1 [--skip-gate live-smoke-b0]

Writes .ai-native/runs/phase-N/latest.json (gitignored). A gate that cannot run for a named external reason is
reported BLOCKED, never PASS. Exit code 0 only if every required, non-blocked gate passed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", type=int, required=True)
    ap.add_argument("--block", action="append", default=[], metavar="GATE=REASON",
                    help="mark a gate BLOCKED with a concrete external reason instead of running it")
    args = ap.parse_args(argv)
    contract_path = ROOT / "docs" / "spec" / "contracts" / f"phase-{args.phase}.json"
    contract = json.loads(contract_path.read_text())
    blocked = dict(b.split("=", 1) for b in args.block)
    results = []
    for gate in contract["gates"]:
        if gate["id"] in blocked:
            results.append({"id": gate["id"], "level": gate["level"], "required": gate.get("required", True),
                            "status": "BLOCKED", "reason": blocked[gate["id"]], "command": gate["command"]})
            print(f"BLOCKED {gate['id']}: {blocked[gate['id']]}")
            continue
        start = time.monotonic()
        try:
            proc = subprocess.run(gate["command"], cwd=ROOT, capture_output=True, text=True,
                                  timeout=gate.get("timeout_seconds", 180), env=os.environ.copy(), check=False)
            status = "PASS" if proc.returncode == 0 else "FAIL"
            tail = (proc.stdout + proc.stderr)[-1500:]
            code = proc.returncode
        except subprocess.TimeoutExpired:
            status, tail, code = "FAIL", "timeout", None
        results.append({"id": gate["id"], "level": gate["level"], "required": gate.get("required", True),
                        "status": status, "exit_code": code, "seconds": round(time.monotonic() - start, 1),
                        "command": gate["command"], "output_tail": tail})
        print(f"{status:7} {gate['id']} ({results[-1]['seconds']}s)")
    evidence = {"phase": args.phase, "contract_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                "commit": git("rev-parse", "HEAD"), "dirty": bool(git("status", "--porcelain")),
                "at": datetime.now(UTC).isoformat(), "gates": results}
    out = ROOT / ".ai-native" / "runs" / f"phase-{args.phase}" / "latest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2) + "\n")
    failed = [r["id"] for r in results if r["required"] and r["status"] == "FAIL"]
    blocked_ids = [r["id"] for r in results if r["status"] == "BLOCKED"]
    verdict = "FAIL" if failed else ("BLOCKED" if blocked_ids else "PASS")
    print(f"verdict: {verdict} commit={evidence['commit'][:12]} dirty={evidence['dirty']} failed={failed} "
          f"blocked={blocked_ids}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
