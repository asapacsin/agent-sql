"""UI backend: runs a system on one question and collects what the page renders (no business logic in the page)."""
from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from agent_sql.config.settings import load_limits, load_model_settings, load_system
from agent_sql.db.executor import Executor, ResultStore
from agent_sql.db.registry import open_database
from agent_sql.llm.base import LLMClient
from agent_sql.llm.pricing import load_pricing
from agent_sql.systems.base import Runtime
from agent_sql.systems.contracts import RunResult, TaskInput
from agent_sql.systems.factory import build_system

UI_SYSTEMS = ["a2", "a1", "b1", "b0"]
UI_DATABASES = ["retailco_dev"]  # the held-out test instance is never exposed in the UI (leakage)


@dataclass
class UIRun:
    result: RunResult
    tables: dict[str, tuple[list[str], list[tuple], bool]] = field(default_factory=dict)  # query_id -> data
    trace_events: list[dict] = field(default_factory=list)


def default_llm() -> LLMClient | None:
    model = load_model_settings()
    if model.provider == "anthropic" and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        return None
    from agent_sql.llm.factory import live_client

    return live_client(model, interactive=True)


def run_question(system_id: str, db_id: str, question: str, llm: LLMClient, as_of: str | None = "2026-01-01") -> UIRun:
    if db_id not in UI_DATABASES:
        raise ValueError(f"database {db_id} is not available in the UI")
    db = open_database(db_id)
    model = load_model_settings().model_copy(update={"benchmark_mode": False})  # interactive: fallbacks shown
    trace_dir = Path(tempfile.mkdtemp(prefix="agent-sql-ui-"))  # per-run trace file: thread-safe across sessions
    rt = Runtime(db, llm, model, load_limits(), load_pricing(), trace_dir)
    system = build_system(rt, load_system(system_id))
    from datetime import date

    task = TaskInput(task_id=f"ui-{int(time.time() * 1000)}", db_id=db_id, question=question,
                     as_of_date=date.fromisoformat(as_of) if as_of else None)
    result = system.run(task)
    events = []
    if result.trace_path:
        events = [json.loads(line) for line in Path(result.trace_path).read_text().splitlines() if line.strip()]
    run = UIRun(result, trace_events=events)
    ex = Executor(db, statement_timeout_s=30, max_rows=1000)
    wanted = set(result.answer.tables) | ({result.answer.final_query_id} if result.answer.final_query_id else set())
    for q in result.queries:
        if q["query_id"] in wanted and q["status"] == "OK":
            rec = ex.run(q["sql"], ResultStore())  # read-only re-execution for display
            if rec.ok and rec.result:
                run.tables[q["query_id"]] = (rec.result.columns, rec.result.rows, rec.result.truncated)
    return run


def public_trace(events: list[dict]) -> list[dict]:
    """Observable action trace: tool actions, observations, controller decisions. Never model reasoning."""
    keep = []
    for e in events:
        if e["type"] == "tool_call":
            keep.append({"step": e["seq"], "action": e["tool"], "purpose": e.get("purpose", ""), "args": e.get("args")})
        elif e["type"] == "tool_result":
            keep.append({"step": e["seq"], "observation": e.get("tool"), "status": e.get("status"),
                         "detail": e.get("summary") or e.get("error_class") or "", "query_id": e.get("query_id"),
                         "rows": e.get("rows")})
        elif e["type"] in ("state", "budget", "validation"):
            keep.append({"step": e["seq"], "decision": e["type"],
                         "detail": {k: v for k, v in e.items() if k not in ("run_id", "task_id", "system_id", "seq",
                                                                             "t", "type")}})
    return keep
