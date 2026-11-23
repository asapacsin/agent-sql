"""A1 - Executable SQL Agent (docs/spec/BASELINES.md section 3): tools, execution feedback, bounded repair."""
from __future__ import annotations

from agent_sql.config.settings import SystemConfig
from agent_sql.orchestration.agent_loop import AgentLoop, LoopLimits, user_message
from agent_sql.systems.b1_retrieval import schema_index
from agent_sql.systems.base import RunContext, Runtime, load_prompts, render_full_schema, task_context
from agent_sql.systems.contracts import RunResult, TaskInput
from agent_sql.tools.registry import A1_DATA_TOOLS, EXPLORATION


class A1System:
    data_tools = A1_DATA_TOOLS
    control_tools = ("submit_answer",)

    def __init__(self, rt: Runtime, cfg: SystemConfig) -> None:
        self.rt, self.cfg = rt, cfg
        self.system_id = cfg.system_id
        self.prompts = load_prompts(cfg.prompts)
        self.flags = {"schema_context": "retrieved", "exploration_tools": "on", "execution_feedback": "full",
                      "repair_budget": 3, **cfg.flags}

    # ---------------------------------------------------------------- context
    def schema_block(self, ctx: RunContext) -> tuple[str, str]:
        mode = self.flags["schema_context"]
        db = self.rt.db
        if mode == "full":
            return "Schema:", render_full_schema(db)
        if mode == "names_only":
            return "Tables (use describe_table for details):", ", ".join(db.catalog.names)
        idx = schema_index(db)
        tables = idx.retrieve(ctx.task.question, ctx.task.evidence, 5)
        ctx.retrieved_tables = list(tables)
        ctx.tool_calls += 1
        ctx.trace.emit("tool_call", tool="retrieve_schema", purpose="initial schema retrieval", args={"top_k": 5})
        ctx.trace.emit("tool_result", tool="retrieve_schema", status="OK", summary=f"tables: {tables}")
        return "Relevant schema (retrieved; other tables may exist):", idx.render(tables, ctx.task.question)

    def tool_names(self) -> list[str]:
        names = list(self.data_tools)
        if self.flags["exploration_tools"] == "off":
            names = [n for n in names if n not in EXPLORATION]
        return names

    def limits(self) -> LoopLimits:
        assert self.cfg.budgets is not None
        return LoopLimits.from_budgets(self.cfg.budgets, int(self.flags["repair_budget"]),
                                       self.rt.limits.task_wall_clock_s)

    def system_prompt(self, heading: str, schema: str, context: str) -> str:
        dialect = "PostgreSQL" if self.rt.db.dialect == "postgres" else "SQLite"
        return self.prompts["system"].render(dialect=dialect, repair_budget=str(self.flags["repair_budget"]),
                                             schema_heading=heading, schema=schema, context=context)

    def run(self, task: TaskInput) -> RunResult:
        ctx = RunContext(self.rt, task, self.system_id)
        ctx.trace.emit("run_start", db_id=task.db_id, model=self.rt.model.model_id, flags=self.flags,
                       prompts={k: p.sha256 for k, p in self.prompts.items()})
        heading, schema = self.schema_block(ctx)
        context = task_context(task, self.rt.db)
        loop = AgentLoop(ctx, system_prompt=self.system_prompt(heading, schema, context),
                         data_tools=self.tool_names(), control_tools=list(self.control_tools), limits=self.limits(),
                         execution_feedback=self.flags["execution_feedback"])
        answer = loop.run(user_message(task.question, []))
        return ctx.result(answer)
