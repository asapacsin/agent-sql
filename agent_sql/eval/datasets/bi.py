"""RetailCo BI task loader. Gold stays on the evaluator side; systems receive TaskInput only."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from agent_sql.config.settings import REPO_ROOT, sha256_file
from agent_sql.systems.contracts import TaskInput

TASK_DIR = REPO_ROOT / "bi_dataset" / "tasks"
RESULT_TYPES = {"aggregate", "ranking", "trend", "cohort"}


@dataclass(frozen=True)
class EvalTask:
    task_id: str
    dataset: str
    split: str
    db_id: str
    question: str
    task_type: str
    gold: dict
    metric_ids: tuple[str, ...] = ()
    evidence: str | None = None
    as_of_date: str | None = None
    difficulty: str | None = None

    def to_input(self) -> TaskInput:
        return TaskInput(task_id=self.task_id, db_id=self.db_id, question=self.question, evidence=self.evidence,
                         as_of_date=date.fromisoformat(self.as_of_date) if self.as_of_date else None)

    @property
    def result_checkable(self) -> bool:
        return self.dataset == "bird" or self.task_type in RESULT_TYPES


def load_bi_tasks(split: str, path: Path | None = None) -> list[EvalTask]:
    path = path or TASK_DIR / f"{split}.jsonl"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        t = json.loads(line)
        out.append(EvalTask(t["task_id"], "bi", t["split"], t["db_id"], t["question"], t["task_type"], t["gold"],
                            tuple(t.get("metric_ids", [])), None, t.get("as_of_date")))
    return out


def manifest_hash(split: str) -> str:
    return sha256_file(TASK_DIR / f"{split}.jsonl")
