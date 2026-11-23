"""Preregistration lock (docs/spec/EXPERIMENTS.md section 2): test-split runs need a listed config hash."""
from __future__ import annotations

import re
from pathlib import Path

from agent_sql.config.settings import REPO_ROOT

PREREG = REPO_ROOT / "experiments" / "preregistration.md"
HASH_LINE = re.compile(r"^\s*-\s*config_hash:\s*`?([0-9a-f]{64})`?", re.MULTILINE)


def registered_hashes(path: Path = PREREG) -> set[str]:
    return set(HASH_LINE.findall(path.read_text())) if path.exists() else set()


def is_preregistered(config_hash: str, path: Path = PREREG) -> bool:
    return config_hash in registered_hashes(path)


def verify(path: Path = PREREG) -> tuple[bool, str]:
    if not path.exists():
        return False, f"not preregistered: {path.relative_to(REPO_ROOT)} does not exist"
    hashes = registered_hashes(path)
    text = path.read_text()
    missing = [h for h in ("H1", "H2", "H3") if h not in text]
    if not hashes or missing:
        return False, f"preregistration incomplete: hashes={len(hashes)} missing hypotheses={missing}"
    return True, f"preregistration OK: {len(hashes)} config hashes locked"
