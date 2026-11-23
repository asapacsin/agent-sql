"""Bounded tool-calling loop (docs/spec/ARCHITECTURE.md section 4). The LLM chooses actions; this code enforces
budgets, failure counting, allowed tools, termination and the answer contract.

States: CONTEXT -> AGENT_TURN -> TOOL_EXEC -> OBSERVE -> AGENT_TURN ... -> ANSWER_CHECK -> DONE
        budget guard -> FINALIZE -> ANSWER_CHECK; failure_count > repair_budget -> DONE_FAILED
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from agent_sql.config.settings import AgentBudgets
from agent_sql.llm.base import ProviderError, json_tool_result
from agent_sql.systems.base import ModelMismatch, RunContext
from agent_sql.systems.contracts import (
    ChartHint,
    Claim,
    Entity,
    EvidenceRef,
    FinalAnswer,
    MetricInterpretation,
    Uncertainty,
)
from agent_sql.tools.registry import ArgError, ToolDef, validate_args
from agent_sql.tools.sql_tools import CONTROL_TOOLS, DATA_TOOLS

FAILURE_STATUSES = {"ERROR", "TIMEOUT", "REJECTED"}


class _BudgetHit(Exception):  # noqa: N818 - internal signal: answer the call with BUDGET_EXHAUSTED
    pass


class Terminate(Exception):  # noqa: N818 - control-flow signal
    def __init__(self, answer: FinalAnswer) -> None:
        super().__init__(answer.status)
        self.answer = answer


@dataclass
class LoopLimits:
    max_llm_calls: int
    max_tool_calls: int
    max_input_tokens: int
    reserve_tokens: int
    max_failures: int  # repair_budget + 1 failures terminate the scope
    wall_clock_s: float

    @classmethod
    def from_budgets(cls, b: AgentBudgets, repair_budget: int, wall_clock_s: float) -> LoopLimits:
        return cls(b.max_llm_calls, b.max_tool_calls, b.max_input_tokens, b.final_answer_reserve_tokens,
                   repair_budget + 1, wall_clock_s)


@dataclass
class LoopState:
    messages: list[dict] = field(default_factory=list)
    scope_failures: int = 0
    nudges: int = 0
    contract_corrections: int = 0
    pending_finalize: str | None = None


def answer_from_args(args: dict) -> FinalAnswer:
    claims = []
    for i, c in enumerate(args.get("claims") or []):
        ev = None
        if c.get("query_id") and c.get("row_index") is not None and c.get("column"):
            ev = EvidenceRef(query_id=c["query_id"], row_index=int(c["row_index"]), column=c["column"])
        claims.append(Claim(claim_id=f"c{i + 1}", text=c.get("text", ""), value=c.get("value"), unit=c.get("unit"),
                            entities=[Entity(**e) for e in c.get("entities") or []], evidence=ev))
    final_qid = args.get("final_query_id")
    tables = sorted({c.evidence.query_id for c in claims if c.evidence} | ({final_qid} if final_qid else set()))
    unc = args.get("uncertainty") or {"level": "medium", "reasons": []}
    return FinalAnswer(status=args["status"], answer_text=str(args.get("answer_text", ""))[:1200],
                       final_query_id=final_qid, claims=claims, tables=tables,
                       charts=[ChartHint(**ch) for ch in args.get("charts") or []],
                       metric_interpretations=[MetricInterpretation(**m) for m in args.get("metric_interpretations") or []],
                       assumptions=list(args.get("assumptions") or []), limitations=list(args.get("limitations") or []),
                       uncertainty=Uncertainty(**unc))


def contract_problems(answer: FinalAnswer, ctx: RunContext) -> list[str]:
    """Deterministic ANSWER_CHECK (not a correctness judgement)."""
    problems = []
    if answer.status == "ANSWERED":
        rec = ctx.store.get(answer.final_query_id) if answer.final_query_id else None
        if rec is None or not rec.ok:
            problems.append("final_query_id must reference a successful execute_readonly_sql result")
    for c in answer.claims:
        if c.is_numeric and answer.status != "ANSWERED":
            problems.append(f"{c.claim_id}: numeric claims are not allowed with status {answer.status}")
        if c.is_numeric and c.evidence is None:
            problems.append(f"{c.claim_id}: numeric claim needs query_id, row_index and column")
        elif c.evidence is not None:
            rec = ctx.store.get(c.evidence.query_id)
            if rec is None or not rec.ok or rec.result is None or c.evidence.column not in rec.result.columns \
                    or not 0 <= c.evidence.row_index < len(rec.result.rows):
                problems.append(f"{c.claim_id}: evidence {c.evidence.query_id}[{c.evidence.row_index}]."
                                f"{c.evidence.column} does not exist")
    return problems


class AgentLoop:
    def __init__(self, ctx: RunContext, *, system_prompt: str, data_tools: list[str], control_tools: list[str],
                 limits: LoopLimits, execution_feedback: str = "full",
                 on_control: Callable[[str, dict, str], dict] | None = None) -> None:
        self.ctx = ctx
        self.system_prompt = system_prompt
        self.tools: dict[str, ToolDef] = {n: DATA_TOOLS[n] for n in data_tools}
        self.tools.update({n: CONTROL_TOOLS[n] for n in control_tools})
        self.limits = limits
        self.feedback = execution_feedback
        self.on_control = on_control  # A2 handles submit_plan / complete_step
        self.state = LoopState()
        self.started = time.monotonic()

    # ---------------------------------------------------------------- guards
    def _budget_exceeded(self) -> str | None:
        ctx, lim = self.ctx, self.limits
        if ctx.llm_calls >= lim.max_llm_calls:
            return "FAILED_TOOL_BUDGET"
        if ctx.usage.total_input >= lim.max_input_tokens - lim.reserve_tokens:
            return "FAILED_TOKEN_BUDGET"
        if time.monotonic() - self.started >= lim.wall_clock_s:
            return "FAILED_TIMEOUT"
        return None

    def _budget_event(self, counter: str, value: float, limit: float, action: str) -> None:
        self.ctx.trace.emit("budget", counter=counter, value=value, limit=limit, action=action)

    # ---------------------------------------------------------------- main
    def run(self, user_content: str, stop_on_answer: bool = True) -> FinalAnswer:
        self.state.messages.append({"role": "user", "content": user_content})
        self.ctx.trace.emit("state", frm="CONTEXT", to="AGENT_TURN", reason="start")
        try:
            while True:
                reason = self._budget_exceeded()
                if reason:
                    self._budget_event("budget", 0, 0, f"finalize:{reason}")
                    return self._finalize(reason)
                done = self._turn(self.tools)
                if done is not None:
                    return done
        except Terminate as t:
            return t.answer
        except ModelMismatch as e:
            return FinalAnswer.failure("FAILED_MODEL_MISMATCH", str(e))
        except ProviderError as e:
            return FinalAnswer.failure("FAILED_PROVIDER", f"{e.kind}: {e}")

    def _turn(self, tools: dict[str, ToolDef]) -> FinalAnswer | None:
        ctx = self.ctx
        resp = ctx.call_llm(system=self.system_prompt, messages=self.state.messages,
                            tools=[t.spec() for t in tools.values()], purpose="agent turn")
        self.state.messages.append({"role": "assistant", "content": resp.content})
        uses = resp.tool_uses()
        if not uses:
            if resp.stop_reason == "max_tokens":
                raise Terminate(FinalAnswer.failure("FAILED_INVALID_OUTPUT", "response truncated at max_tokens"))
            self.state.nudges += 1
            if self.state.nudges > 2:
                raise Terminate(FinalAnswer.failure("FAILED_INVALID_OUTPUT", "model stopped without submit_answer"))
            self.state.messages.append({"role": "user", "content": "Continue with tool calls. When finished, call "
                                        "submit_answer (status ANSWERED or INSUFFICIENT_DATA)."})
            return None
        results = []
        final: FinalAnswer | None = None
        for use in uses:
            name, args, uid = use["name"], use.get("input") or {}, use["id"]
            if self.state.pending_finalize:
                results.append(json_tool_result(uid, {"status": "BUDGET_EXHAUSTED"}, True))
                continue
            try:
                payload, is_error = self._dispatch(name, args, tools)
            except _BudgetHit:
                payload, is_error = {"status": "BUDGET_EXHAUSTED"}, True
            if isinstance(payload, FinalAnswer):
                final = payload
                payload, is_error = {"status": "ACCEPTED"}, False
            results.append(json_tool_result(uid, payload, is_error))
        self.state.messages.append({"role": "user", "content": results})
        if final is not None:
            return final
        if self.state.pending_finalize:
            return self._finalize(self.state.pending_finalize)
        return None

    def _dispatch(self, name: str, args: dict, tools: dict[str, ToolDef]):  # noqa: ANN202
        ctx = self.ctx
        tool = tools.get(name)
        purpose = str(args.get("purpose", ""))[:200] if isinstance(args, dict) else ""
        ctx.trace.emit("tool_call", tool=name, purpose=purpose, args=args)
        if tool is None:
            ctx.tool_calls += 1
            self._check_tool_budget()
            out = {"status": "TOOL_NOT_ALLOWED", "message": f"{name} is not available here",
                   "allowed": sorted(tools)}
            ctx.trace.emit("tool_result", tool=name, status="TOOL_NOT_ALLOWED")
            return out, True
        try:
            validate_args(tool.schema, args)
        except ArgError as e:
            if tool.data_tool:
                ctx.tool_calls += 1
                self._check_tool_budget()
            ctx.trace.emit("tool_result", tool=name, status="INVALID_ARGS", summary=str(e))
            return {"status": "INVALID_ARGS", "detail": str(e)}, True
        if not tool.data_tool:
            return self._control(name, args)
        ctx.tool_calls += 1
        self._check_tool_budget()
        kwargs = {k: v for k, v in args.items()}
        before = ctx.failures
        try:
            out = tool.fn(ctx, **kwargs)
        except Exception as e:  # noqa: BLE001 - a tool bug or odd argument combination is an observation
            out = {"status": "ERROR", "error_class": "TOOL_ERROR", "message": f"{type(e).__name__}: {str(e)[:200]}"}
        status = out.get("status", "OK")
        failed = status in FAILURE_STATUSES and name in ("execute_readonly_sql", "validate_sql")
        if failed:
            if name == "validate_sql":
                ctx.failures += 1
            self.state.scope_failures += 1
            if self.state.scope_failures > 1:
                ctx.repairs += 1
        elif name == "execute_readonly_sql" and ctx.failures == before and self.state.scope_failures:
            ctx.repairs += 1  # a successful attempt after a failure is also a repair attempt
        ctx.trace.emit("tool_result", tool=name, status=status, error_class=out.get("error_class"),
                       query_id=out.get("query_id"), rows=out.get("row_count"), truncated=out.get("truncated"),
                       summary=out.get("message") or out.get("hint"))
        if failed and self.state.scope_failures >= self.limits.max_failures:
            self._budget_event("failures", self.state.scope_failures, self.limits.max_failures, "terminate")
            raise Terminate(FinalAnswer.failure("FAILED_REPAIR_BUDGET",
                                                f"{self.state.scope_failures} SQL failures exhausted the repair budget"))
        if self.feedback == "binary" and name in ("execute_readonly_sql", "validate_sql", "get_query_result_metadata"):
            out = {"status": status, "query_id": out.get("query_id")}  # AB4: no messages, hints or values
        return out, status not in ("OK",)

    def _check_tool_budget(self) -> None:
        if self.ctx.tool_calls > self.limits.max_tool_calls:
            self._budget_event("tool_calls", self.ctx.tool_calls, self.limits.max_tool_calls, "finalize")
            self.state.pending_finalize = "FAILED_TOOL_BUDGET"
            raise _BudgetHit()

    def _control(self, name: str, args: dict):  # noqa: ANN202
        if name == "submit_answer":
            answer = answer_from_args(args)
            problems = contract_problems(answer, self.ctx)
            self.ctx.trace.emit("validation", check_id="ANSWER_CHECK", result="PASS" if not problems else "FAIL",
                                detail="; ".join(problems))
            if not problems:
                return answer, False
            self.state.contract_corrections += 1
            if self.state.contract_corrections > 1:
                raise Terminate(FinalAnswer.failure("FAILED_INVALID_OUTPUT", "answer contract violated twice: "
                                                    + "; ".join(problems)))
            return {"status": "REJECTED", "problems": problems,
                    "instruction": "Fix the answer and call submit_answer again."}, True
        if self.on_control is None:
            return {"status": "TOOL_NOT_ALLOWED"}, True
        return self.on_control(name, args, ""), False

    def _finalize(self, reason: str) -> FinalAnswer:
        """One last LLM call with only submit_answer; never fabricate on failure."""
        ctx = self.ctx
        ctx.trace.emit("state", frm="AGENT_TURN", to="FINALIZE", reason=reason)
        note = (f"Budget limit reached ({reason}). Call submit_answer now using only results you already have, or use "
                "status INSUFFICIENT_DATA. Do not call any other tool.")
        last = self.state.messages[-1] if self.state.messages else None
        if last and last["role"] == "user" and isinstance(last["content"], list):
            last["content"].append({"type": "text", "text": note})
        else:
            self.state.messages.append({"role": "user", "content": note})
        if ctx.usage.total_input >= self.limits.max_input_tokens or ctx.llm_calls > self.limits.max_llm_calls:
            return FinalAnswer.failure(reason, "budget exhausted before a final answer")
        try:
            resp = ctx.call_llm(system=self.system_prompt, messages=self.state.messages,
                                tools=[CONTROL_TOOLS["submit_answer"].spec()], purpose="finalize")
        except (ProviderError, ModelMismatch) as e:
            return FinalAnswer.failure("FAILED_PROVIDER", str(e))
        for use in resp.tool_uses():
            if use["name"] != "submit_answer":
                continue
            try:
                validate_args(CONTROL_TOOLS["submit_answer"].schema, use.get("input") or {})
            except ArgError:
                break
            answer = answer_from_args(use["input"])
            if not contract_problems(answer, ctx):
                answer.limitations.append(f"answered under budget limit ({reason})")
                return answer
        return FinalAnswer.failure(reason, "no valid answer within budget")


def user_message(task_question: str, context_blocks: list[str]) -> str:
    return "\n\n".join([f"Question: {task_question}", *[b for b in context_blocks if b]])


def dumps(obj) -> str:  # noqa: ANN001
    return json.dumps(obj, default=str)
