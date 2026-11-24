"""A2 - Planning and Verification Agent (docs/spec/BASELINES.md section 3, ARCHITECTURE.md section 4.3).

States: PLAN (submit_plan) -> STEPS (data tools + complete_step, deterministic INSPECT per step) -> VERIFY
(V1-V8, <= 4 verification queries) -> ANSWER (submit_answer, V1 grounding with one revision) -> DONE.
Flags: planning=off skips PLAN (single implicit step); verification=off skips INSPECT/VERIFY/revision.
"""
from __future__ import annotations

from datetime import date

from agent_sql.config.settings import SystemConfig
from agent_sql.db.executor import ResultStore
from agent_sql.orchestration.agent_loop import (
    AgentLoop,
    Terminate,
    answer_from_args,
    apply_answer_projection,
    contract_problems,
    user_message,
)
from agent_sql.systems.a1_agent import A1System
from agent_sql.systems.base import RunContext, Runtime
from agent_sql.systems.contracts import FinalAnswer, RunResult, TaskInput, ValidationCheck
from agent_sql.tools.registry import A2_DATA_TOOLS, EXPLORATION
from agent_sql.verification.checks import CheckInput, QueryView, canonical_totals, model_safe, run_checks
from agent_sql.verification.metrics_registry import load_registry


def inspect_result(ctx: RunContext, query_id: str) -> list[str]:
    """Deterministic INSPECT findings for a step result."""
    rec = ctx.store.get(query_id)
    if rec is None or not rec.ok or rec.result is None:
        return ["step result is not a successful query"]
    res, findings = rec.result, []
    if not res.rows:
        findings.append("EMPTY: the result has no rows; check filter literals and the period")
    if res.truncated:
        findings.append("TRUNCATED: result exceeded the stored-row cap; aggregate further")
    for i, c in enumerate(res.columns):
        if res.rows:
            nulls = sum(1 for r in res.rows if r[i] is None)
            if nulls / len(res.rows) > 0.5:
                findings.append(f"NULLS: column {c} is {nulls}/{len(res.rows)} null")
    if len(res.rows) > 1:
        dups = len(res.rows) - len(set(map(tuple, res.rows)))
        if dups:
            findings.append(f"DUPLICATES: {dups} duplicate rows (possible join fan-out)")
    return findings


class A2Loop(AgentLoop):
    def __init__(self, ctx: RunContext, system: A2System, **kw) -> None:  # noqa: ANN003
        super().__init__(ctx, **kw)
        self.sys = system
        self.flags = system.flags
        self.budgets = system.cfg.budgets
        self.data_tool_names = system.tool_names()
        self.plan: dict | None = None
        self.replans = 0
        self.completed: dict[str, str | None] = {}
        self.verify_started = False
        self.verification_queries = 0
        self.revisions = 0
        self.comparisons: list[dict] = []
        self.report_checks: list[ValidationCheck] = []
        self.phase = "PLAN" if self.flags["planning"] == "on" else "STEPS"
        if self.phase == "STEPS":
            self.plan = {"steps": [{"step_id": "s1", "goal": "answer the question", "expected_output": "table",
                                    "metric_ids": [], "depends_on": []}], "periods": [], "assumptions": []}
        self._set_tools()

    def _set_tools(self) -> None:
        from agent_sql.tools.sql_tools import CONTROL_TOOLS, DATA_TOOLS

        if self.phase == "PLAN":
            names, control = [], ["submit_plan"]
        elif self.phase == "STEPS":
            names = self.data_tool_names
            control = ["complete_step"] + (["submit_plan"] if self.flags["planning"] == "on" else [])
        else:  # VERIFY / ANSWER
            names, control = self.data_tool_names, ["submit_answer"]
        self.tools = {n: DATA_TOOLS[n] for n in names}
        self.tools.update({n: CONTROL_TOOLS[n] for n in control})

    def _transition(self, to: str, reason: str) -> None:
        self.ctx.trace.emit("state", frm=self.phase, to=to, reason=reason)
        self.phase = to
        self._set_tools()

    # ---------------------------------------------------------------- dispatch gating
    def _dispatch(self, name: str, args: dict, tools):  # noqa: ANN001, ANN202
        if name == "execute_readonly_sql" and self.phase in ("VERIFY", "ANSWER") and name in self.tools:
            if self.verification_queries >= (self.budgets.max_verification_queries or 4):
                return {"status": "BUDGET_EXHAUSTED", "message": "verification-query budget used; submit_answer"}, True
            self.verification_queries += 1
            self.ctx.verification_queries += 1
        payload, err = super()._dispatch(name, args, tools)
        if name == "compare_query_results" and isinstance(payload, dict) and payload.get("status") == "OK":
            self.comparisons.append(payload)
        if self.ctx.failures >= (self.budgets.max_failures_per_task or 6):
            raise Terminate(FinalAnswer.failure("FAILED_REPAIR_BUDGET", "task failure cap reached"))
        return payload, err

    # ---------------------------------------------------------------- control tools
    def _control(self, name: str, args: dict):  # noqa: ANN202
        if name == "submit_plan":
            return self._submit_plan(args)
        if name == "complete_step":
            return self._complete_step(args)
        if name == "submit_answer":
            return self._submit_answer(args)
        return {"status": "TOOL_NOT_ALLOWED"}, True

    def _plan_problems(self, plan: dict) -> list[str]:
        problems, reg = [], load_registry()
        steps = plan.get("steps", [])
        if not 1 <= len(steps) <= (self.budgets.max_plan_steps or 5):
            problems.append(f"plan must have 1..{self.budgets.max_plan_steps or 5} steps")
        ids = [s["step_id"] for s in steps]
        if len(set(ids)) != len(ids):
            problems.append("duplicate step_id")
        for s in steps:
            for m in s.get("metric_ids", []):
                if self.sys.is_bi and m not in reg:
                    problems.append(f"{s['step_id']}: unknown metric_id {m!r} (use registry ids)")
            for d in s.get("depends_on", []):
                if d not in ids or ids.index(d) >= ids.index(s["step_id"]):
                    problems.append(f"{s['step_id']}: depends_on {d!r} must reference an earlier step")
        for p in plan.get("periods", []):
            try:
                if date.fromisoformat(p["start"]) >= date.fromisoformat(p["end"]):
                    problems.append(f"period {p['label']}: start must be before end (half-open)")
            except ValueError:
                problems.append(f"period {p.get('label')}: dates must be YYYY-MM-DD")
        return problems

    def _submit_plan(self, args: dict):  # noqa: ANN202
        if self.plan is not None and self.phase != "PLAN":
            if self.replans >= (self.budgets.max_replans or 1):
                return {"status": "REJECTED", "problems": ["re-plan budget used"]}, True
            self.replans += 1
        problems = self._plan_problems(args)
        self.ctx.trace.emit("validation", check_id="PLAN_CHECK", result="FAIL" if problems else "PASS",
                            detail="; ".join(problems))
        if problems:
            if self.phase == "PLAN" and self.replans < (self.budgets.max_replans or 1):
                self.replans += 1
                return {"status": "REJECTED", "problems": problems}, True
            # still invalid: fall back to a single-step plan (logged)
            args = {"steps": [{"step_id": "s1", "goal": "answer the question", "expected_output": "table",
                               "metric_ids": [], "depends_on": []}], "periods": [], "assumptions": []}
            self.ctx.trace.emit("validation", check_id="PLAN_FALLBACK", result="WARN", detail="single-step plan")
        self.plan = args
        self.completed = {}
        self.scope_reset()
        self._transition("STEPS", "plan accepted")
        return {"status": "ACCEPTED", "steps": [s["step_id"] for s in args["steps"]],
                "next": f"work on step {args['steps'][0]['step_id']}, then call complete_step"}, False

    def scope_reset(self) -> None:
        self.state.scope_failures = 0

    def _complete_step(self, args: dict):  # noqa: ANN202
        assert self.plan is not None
        ids = [s["step_id"] for s in self.plan["steps"]]
        sid = args["step_id"]
        if sid not in ids:
            return {"status": "REJECTED", "problems": [f"unknown step {sid!r}; steps: {ids}"]}, True
        qid, reason = args.get("query_id"), args.get("unanswerable_reason")
        if (qid is None) == (reason is None):
            return {"status": "REJECTED", "problems": ["give exactly one of query_id or unanswerable_reason"]}, True
        if qid is not None:
            rec = self.ctx.store.get(qid)
            if rec is None or not rec.ok:
                return {"status": "REJECTED", "problems": [f"{qid} is not a successful query"]}, True
        self.completed[sid] = qid
        self.scope_reset()  # per-step repair budget
        findings = inspect_result(self.ctx, qid) if (qid and self.flags["verification"] == "on") else []
        if findings:
            self.ctx.trace.emit("validation", check_id="INSPECT", result="WARN", detail="; ".join(findings))
        remaining = [i for i in ids if i not in self.completed]
        out = {"status": "ACCEPTED", "inspection": findings or ["no issues found"], "remaining_steps": remaining}
        if not remaining:
            out.update(self._enter_verify())
        return out, False

    def _check_input(self, answer: FinalAnswer) -> CheckInput:
        queries = {}
        for rec in self.ctx.store.all():
            r = rec.result
            queries[rec.query_id] = QueryView(rec.query_id, rec.sql, rec.status, r.columns if r else [],
                                              r.rows if r else [], bool(r and r.truncated))
        periods = (self.plan or {}).get("periods", [])
        metric_ids = [m for s in (self.plan or {}).get("steps", []) for m in s.get("metric_ids", [])]
        totals = {}
        if self.sys.is_bi and periods:
            def run_template(sql: str):  # noqa: ANN202 - trusted canonical template, read-only connection
                rec = self.ctx.executor.run(sql, ResultStore())
                return rec.result.rows[0][0] if rec.ok and rec.result and rec.result.rows else None

            totals = canonical_totals(metric_ids, periods, run_template)
        return CheckInput(answer, queries, self.ctx.rt.db.dialect, self.sys.is_bi, periods, metric_ids,
                          self.comparisons, totals)

    def _enter_verify(self) -> dict:
        self._transition("VERIFY", "all steps complete")
        if self.flags["verification"] != "on":
            return {"next": "all steps complete: call submit_answer"}
        last = next((q for q in reversed(list(self.completed.values())) if q), None)
        probe = FinalAnswer(status="ANSWERED", final_query_id=last)
        report = run_checks(self._check_input(probe))
        failing = model_safe(report.checks)  # no canonical values reach the model (review finding)
        for c in report.checks:
            self.ctx.trace.emit("validation", check_id=c.id, result=c.result, detail=c.detail)
        return {"verification": failing or ["all applicable checks passed"],
                "next": (f"You may run up to {self.budgets.max_verification_queries or 4} verification queries "
                         "(e.g. compare_query_results against a total), then call submit_answer.")}

    def _submit_answer(self, args: dict):  # noqa: ANN202
        if self.phase not in ("VERIFY", "ANSWER"):
            return {"status": "REJECTED", "problems": ["complete all plan steps first (complete_step)"]}, True
        answer = answer_from_args(args)
        problems = contract_problems(answer, self.ctx)
        if problems:
            self.state.contract_corrections += 1
            if self.state.contract_corrections > 1:
                raise Terminate(FinalAnswer.failure("FAILED_INVALID_OUTPUT", "; ".join(problems)))
            return {"status": "REJECTED", "problems": problems}, True
        if self.flags["verification"] != "on":
            return answer, False
        report = run_checks(self._check_input(answer))
        v1 = next(c for c in report.checks if c.id == "V1")
        if v1.result == "FAIL" and self.revisions == 0:
            self.revisions += 1
            self.ctx.trace.emit("validation", check_id="V1", result="FAIL", detail=v1.detail + " (revision requested)")
            return {"status": "REJECTED", "problems": model_safe([v1]) + [{"claims": v1.detail.split(": ", 1)[-1]}],
                    "instruction": "Fix the cited cells or values."}, True
        answer.validation = report
        self._uncertainty_floor(answer)
        for c in report.checks:
            self.ctx.trace.emit("validation", check_id=c.id, result=c.result, detail=c.detail)
        return answer, False

    def _uncertainty_floor(self, answer: FinalAnswer) -> None:
        reasons = [f"{c.id} {c.result}: {c.detail}" for c in answer.validation.checks if c.result == "FAIL"]
        if any(c.id == "V7" and c.result != "PASS" for c in answer.validation.checks):
            reasons.append("result truncated")
        if answer.status == "INSUFFICIENT_DATA":
            reasons.append("required data missing")
        if reasons and answer.uncertainty.level != "high":
            self.ctx.trace.emit("validation", check_id="UNCERTAINTY_FLOOR", result="WARN",
                                detail=f"raised from {answer.uncertainty.level} to high")
            answer.uncertainty.level = "high"
            answer.uncertainty.reasons += reasons


def _a2_finalize(self, reason: str) -> FinalAnswer:  # noqa: ANN001
    """FINALIZE for A2 also runs V1-V8 and the uncertainty floor (review finding)."""
    answer = AgentLoop._finalize(self, reason)
    if answer.status in ("ANSWERED", "INSUFFICIENT_DATA") and self.flags["verification"] == "on":
        answer.validation = run_checks(self._check_input(answer))
        self._uncertainty_floor(answer)
    return answer


A2Loop._finalize = _a2_finalize


class A2System(A1System):
    data_tools = A2_DATA_TOOLS

    def __init__(self, rt: Runtime, cfg: SystemConfig) -> None:
        super().__init__(rt, cfg)
        self.flags = {"planning": "on", "verification": "on", **self.flags}
        self.is_bi = rt.db.db_id.startswith("retailco_")

    def tool_names(self) -> list[str]:
        names = list(A2_DATA_TOOLS)
        if self.flags["exploration_tools"] == "off":
            names = [n for n in names if n not in EXPLORATION]
        return names

    def system_prompt(self, heading: str, schema: str, context: str) -> str:
        dialect = "PostgreSQL" if self.rt.db.dialect == "postgres" else "SQLite"
        reg = load_registry()
        role = "plan" if self.flags["planning"] == "on" else "system"
        base = self.prompts["system"].render(
            dialect=dialect, repair_budget=str(self.flags["repair_budget"]), schema_heading=heading, schema=schema,
            context=context, max_steps=str(self.cfg.budgets.max_plan_steps or 5),
            max_verification=str(self.cfg.budgets.max_verification_queries or 4),
            metric_ids=", ".join(reg.metrics) if self.is_bi else "(none for this database)")
        if role == "plan":
            return base + "\n\n" + self.prompts["plan"].render(max_steps=str(self.cfg.budgets.max_plan_steps or 5))
        return base

    def run(self, task: TaskInput) -> RunResult:
        ctx = RunContext(self.rt, task, self.system_id)
        ctx.trace.emit("run_start", db_id=task.db_id, model=self.rt.model.model_id, flags=self.flags,
                       prompts={k: p.sha256 for k, p in self.prompts.items()})
        heading, schema = self.schema_block(ctx)
        from agent_sql.systems.base import task_context

        context = task_context(task, self.rt.db)
        loop = A2Loop(ctx, self, system_prompt=self.system_prompt(heading, schema, context), data_tools=[],
                      control_tools=[], limits=self.limits(), execution_feedback=self.flags["execution_feedback"])
        start = ("Start by calling submit_plan." if self.flags["planning"] == "on"
                 else "Work on the question; call complete_step(step_id='s1', ...) with your result query, then "
                      "submit_answer.")
        answer = loop.run(user_message(task.question, [start]))
        if self.flags["verification"] != "on":
            answer.validation.status = "NOT_RUN"
        return ctx.result(apply_answer_projection(answer, ctx))
