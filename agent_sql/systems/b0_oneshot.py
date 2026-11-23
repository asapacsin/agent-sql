"""B0 - one-shot SQL baseline (docs/spec/BASELINES.md section 3): one generation, one execution, no retries."""
from __future__ import annotations

import json

from agent_sql.config.settings import SystemConfig
from agent_sql.llm.base import ProviderError
from agent_sql.systems.base import ModelMismatch, RunContext, Runtime, load_prompts, render_full_schema, task_context
from agent_sql.systems.composer import compose
from agent_sql.systems.contracts import FinalAnswer, RunResult, TaskInput

SQL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["sql"],
              "properties": {"sql": {"type": "string"}}}


class OneShotSystem:
    """Shared one-shot pipeline; subclasses choose the schema context (B0 full, B1 retrieved)."""

    def __init__(self, rt: Runtime, cfg: SystemConfig) -> None:
        self.rt, self.cfg = rt, cfg
        self.system_id = cfg.system_id
        self.prompts = load_prompts(cfg.prompts)

    def schema_context(self, ctx: RunContext) -> str:
        return render_full_schema(self.rt.db)

    def generate_sql(self, ctx: RunContext, schema: str, context: str) -> str | None:
        dialect = "PostgreSQL" if self.rt.db.dialect == "postgres" else "SQLite"
        system = self.prompts["sql"].render(dialect=dialect, schema=schema, context=context)
        resp = ctx.call_llm(system=system, messages=[{"role": "user", "content": ctx.task.question}],
                            output_schema=SQL_SCHEMA, purpose="generate SQL")
        try:
            return str(json.loads(resp.text())["sql"])
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def run(self, task: TaskInput) -> RunResult:
        ctx = RunContext(self.rt, task, self.system_id)
        ctx.trace.emit("run_start", db_id=task.db_id, model=self.rt.model.model_id,
                       prompts={k: p.sha256 for k, p in self.prompts.items()})
        context = task_context(task, self.rt.db)
        try:
            ctx.trace.emit("state", frm="INIT", to="GENERATE", reason="one-shot")
            sql = self.generate_sql(ctx, self.schema_context(ctx), context)
            if sql is None:
                return ctx.result(FinalAnswer.failure("FAILED_INVALID_OUTPUT", "SQL generation returned invalid JSON"))
            ctx.tool_calls += 1
            rec = ctx.execute(sql, purpose="single execution")
            if not rec.ok:
                return ctx.result(FinalAnswer.failure("FAILED_EXECUTION", f"{rec.error_class}: {rec.message}"))
            if task.db_id.startswith("retailco_") and "composer" in self.prompts:
                answer = compose(ctx, self.prompts["composer"], rec, context)
            else:
                answer = FinalAnswer(status="ANSWERED", answer_text=f"Result of {rec.query_id}",
                                     final_query_id=rec.query_id, tables=[rec.query_id])
            return ctx.result(answer)
        except ModelMismatch as e:
            return ctx.result(FinalAnswer.failure("FAILED_MODEL_MISMATCH", str(e)))
        except ProviderError as e:
            return ctx.result(FinalAnswer.failure("FAILED_PROVIDER", f"{e.kind}: {e}"))


class B0System(OneShotSystem):
    pass
