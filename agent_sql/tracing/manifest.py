"""Run manifest: binds results to source revision, configs, prompts, datasets, model and pricing."""
from __future__ import annotations

import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from agent_sql.config.settings import REPO_ROOT


def git_state(root: Path = REPO_ROOT) -> dict:
    def run(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=False).stdout.strip()

    status = run("status", "--porcelain", "--untracked-files=no")
    return {"git_sha": run("rev-parse", "HEAD") or None, "dirty": bool(status)}


def build_manifest(*, experiment_id: str, config_hash: str, prompts: dict[str, str], datasets: dict[str, str],
                   model_id: str, pricing_version: str, seed: int, extra: dict | None = None) -> dict:
    try:
        import anthropic

        sdk = anthropic.__version__
    except Exception:  # noqa: BLE001
        sdk = None
    return {"experiment_id": experiment_id, **git_state(), "config_hash": config_hash, "prompt_sha256": prompts,
            "dataset_sha256": datasets, "model_id": model_id, "anthropic_sdk": sdk, "python": sys.version.split()[0],
            "platform": platform.platform(), "pricing_version": pricing_version, "seed": seed,
            "started_at": datetime.now(UTC).isoformat(), **(extra or {})}
