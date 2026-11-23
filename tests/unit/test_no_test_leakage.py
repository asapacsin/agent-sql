"""No prompt (and no few-shot block) contains a held-out test/study question or gold SQL (review finding)."""
from __future__ import annotations

import json

from agent_sql.config.settings import REPO_ROOT


def test_prompts_do_not_contain_held_out_items():
    texts = "\n".join(p.read_text() for p in (REPO_ROOT / "prompts").rglob("*.md")).lower()
    for split in ("test", "study"):
        for line in (REPO_ROOT / "bi_dataset" / "tasks" / f"{split}.jsonl").read_text().splitlines():
            t = json.loads(line)
            assert t["question"].lower() not in texts, t["task_id"]
            assert t["gold"]["sql"].lower()[:120] not in texts, t["task_id"]
