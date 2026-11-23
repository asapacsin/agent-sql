"""Versioned prompt registry. Prompts are files prompts/<system>/<name>.v<N>.md; never edited in place."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from agent_sql.config.settings import REPO_ROOT, sha256_text

VERSIONED = re.compile(r"^[a-z0-9_]+/[a-z0-9_]+\.v\d+\.md$")


@dataclass(frozen=True)
class Prompt:
    path: str
    text: str
    sha256: str

    def render(self, **values: str) -> str:
        out = self.text
        for key, value in values.items():
            out = out.replace("{{" + key + "}}", value)
        missing = re.findall(r"\{\{([a-z_]+)\}\}", out)
        if missing:
            raise KeyError(f"unfilled prompt variables in {self.path}: {sorted(set(missing))}")
        return out


def load_prompt(rel_path: str, root: Path = REPO_ROOT) -> Prompt:
    if not VERSIONED.match(rel_path):
        raise ValueError(f"prompt path must be <dir>/<name>.v<N>.md, got {rel_path!r}")
    text = (root / "prompts" / rel_path).read_text(encoding="utf-8")
    return Prompt(rel_path, text, sha256_text(text))
