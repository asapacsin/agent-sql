"""Score predictions against gold by independent re-execution (docs/spec/BENCHMARK.md sections 4-5)."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import sqlglot
from sqlglot import exp

from agent_sql.db.executor import Database, Executor, ResultStore
from agent_sql.eval.comparators import (
    bird_execution_match,
    score_bi_diagnostic,
    score_bi_insufficient,
    score_bi_result,
)
from agent_sql.eval.datasets.bi import EvalTask
from agent_sql.eval.failure_taxonomy import auto_failure_code
from agent_sql.systems.contracts import FinalAnswer, RunResult
from agent_sql.verification.grounding import check_grounding

POSTHOC_CHECKS_VERSION = "v1:V1,V6,V7"  # V2-V5,V8 join in Phase 4


def tables_in(sql: str, dialect: str) -> set[str]:
    try:
        tree = sqlglot.parse_one(sql, read="postgres" if dialect == "postgres" else "sqlite")
    except Exception:  # noqa: BLE001
        return set()
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    return {t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes}


def _with_gold_values(spec: dict, gold) -> dict:  # noqa: ANN001
    """Inject the evaluator-computed gold value into findings that require a value (BENCHMARK.md 4.2)."""
    value = gold.result.rows[0][0] if gold.ok and gold.result and gold.result.rows else None
    findings = [{**f, "gold_value": value} if f.get("require_value") else f for f in spec["required_findings"]]
    return {**spec, "required_findings": findings}


@dataclass
class TaskScore:
    task_id: str
    system_id: str
    run_index: int
    dataset: str
    task_type: str
    status: str
    result_checkable: bool
    executed: bool              # M01
    correct: bool | None        # M02 (None when not result-checkable)
    success: bool               # M03
    detail: str
    had_failure: bool           # M04 denominator
    recovered_exec: bool | None
    recovered_success: bool | None
    tables_exact: bool | None   # M05
    tables_precision: float | None
    tables_recall: float | None
    tool_calls: int             # M06
    llm_calls: int
    latency_s: float            # M07
    llm_latency_s: float
    db_latency_s: float
    input_tokens: int           # M08
    output_tokens: int
    cache_read_input_tokens: int
    cache_creation_input_tokens: int
    cost_usd: float             # M09
    uncached_cost_usd: float
    validation_failed: bool     # M11 (post-hoc)
    posthoc_checks: list
    numeric_claims: int         # S01
    grounded_claims: int
    abstained: bool             # S04 (INSUFFICIENT_DATA status)
    silent_wrong: bool          # S05
    self_service: bool          # S06
    failure_code: str | None
    set_f1: float | None = None
    retrieval_recall: float | None = None  # S03

    def to_dict(self) -> dict:
        return asdict(self)


class Scorer:
    def __init__(self, databases: dict[str, Database], timeout_s: float, max_rows: int) -> None:
        self.dbs = databases
        self.timeout_s, self.max_rows = timeout_s, max_rows
        self._cache: dict[tuple[str, str], object] = {}

    def _exec(self, db_id: str, sql: str):  # noqa: ANN202
        key = (db_id, sql)
        if key not in self._cache:
            ex = Executor(self.dbs[db_id], statement_timeout_s=self.timeout_s, max_rows=self.max_rows)
            self._cache[key] = ex.run(sql, ResultStore())
        return self._cache[key]

    def _gold(self, task: EvalTask):  # noqa: ANN202
        ex = Executor(self.dbs[task.db_id], statement_timeout_s=self.timeout_s, max_rows=self.max_rows,
                      validate=False)  # gold is trusted evaluator SQL, still on the read-only connection
        key = (task.db_id, "GOLD:" + task.gold["sql"])
        if key not in self._cache:
            self._cache[key] = ex.run(task.gold["sql"], ResultStore())
        return self._cache[key]

    def _events(self, db_id: str) -> list[dict]:
        rec = self._exec(db_id, "SELECT be.event_id, be.event_type, r.region_name FROM business_events be "
                                "LEFT JOIN regions r ON r.region_id = be.region_id")
        return [{"event_id": a, "event_type": b, "region_name": c} for a, b, c in (rec.result.rows if rec.ok else [])]

    def score(self, task: EvalTask, pred: RunResult, run_index: int = 0) -> TaskScore:
        ans: FinalAnswer = pred.answer
        dialect = self.dbs[task.db_id].dialect
        queries = {q["query_id"]: q for q in pred.queries}
        final_sql = queries.get(ans.final_query_id, {}).get("sql") if ans.final_query_id else None
        rec = self._exec(task.db_id, final_sql) if final_sql else None
        executed = bool(rec is not None and rec.ok)
        spec = task.gold.get("answer_spec", {})
        correct: bool | None = None
        detail = ""
        set_f1 = None
        gold = self._gold(task)
        if not gold.ok:
            raise RuntimeError(f"gold SQL failed for {task.task_id}: {gold.message}")
        if task.dataset == "bird":
            correct = executed and bird_execution_match(rec.result.rows, gold.result.rows)
            success = bool(correct) and ans.status == "ANSWERED"
            detail = "EX match" if correct else "EX mismatch"
        elif task.task_type in ("aggregate", "ranking", "trend", "cohort"):
            if executed:
                v = score_bi_result(spec, rec.result.columns, rec.result.rows, gold.result.columns, gold.result.rows)
                correct, detail, set_f1 = v.passed, v.detail, v.score
            else:
                correct, detail = False, "final SQL not executable"
            success = bool(correct) and ans.status == spec.get("expected_status", "ANSWERED")
        elif task.task_type == "diagnostic":
            spec = _with_gold_values(spec, gold)
            v = score_bi_diagnostic(spec, ans, self._events(task.db_id))
            success, detail = v.passed, v.detail
        elif task.task_type == "insufficient":
            v = score_bi_insufficient(spec, ans)
            success, detail = v.passed, v.detail
        else:
            raise ValueError(task.task_type)

        # M04 recovery
        statuses = [q["status"] for q in pred.queries]
        had_failure = any(s != "OK" for s in statuses)
        recovered_exec = executed if had_failure else None
        recovered_success = success if had_failure else None
        # M05 table selection
        tables_exact = tables_p = tables_r = None
        if task.result_checkable and final_sql:
            pt, gt = tables_in(final_sql, dialect), tables_in(task.gold["sql"], dialect)
            if gt:
                tp = len(pt & gt)
                tables_exact = pt == gt
                tables_p = tp / len(pt) if pt else 0.0
                tables_r = tp / len(gt)
        # post-hoc validation V1, V6, V7 (same code for every system)
        checks = []

        def lookup(qid: str, row: int, col: str):  # noqa: ANN202
            q = queries.get(qid)
            if not q or q["status"] != "OK":
                return False, None
            r = self._exec(task.db_id, q["sql"])
            if not r.ok or col not in r.result.columns or not (0 <= row < len(r.result.rows)):
                return False, None
            return True, r.result.rows[row][r.result.columns.index(col)]

        v1, bad = check_grounding(ans.claims, lookup)
        checks.append(v1.model_dump())
        if ans.status == "ANSWERED":
            checks.append({"id": "V6", "result": "FAIL" if executed and not rec.result.rows else "PASS",
                           "detail": "final result empty" if executed and not rec.result.rows else ""})
            trunc = bool(executed and rec.result.truncated)
            disclosed = any("trunc" in x.lower() for x in ans.limitations)
            checks.append({"id": "V7", "result": "FAIL" if trunc and not disclosed else ("WARN" if trunc else "PASS"),
                           "detail": ""})
        validation_failed = any(c["result"] == "FAIL" for c in checks)
        numeric = [c for c in ans.claims if c.is_numeric]
        abstained = ans.status == "INSUFFICIENT_DATA"
        silent_wrong = (ans.status == "ANSWERED" and not success
                        and not any(c["result"] in ("FAIL", "WARN") for c in checks)
                        and ans.validation.status in ("PASSED", "NOT_RUN"))
        self_service = success and task.dataset == "bi" and not validation_failed
        last_error = next((q["error_class"] for q in reversed(pred.queries) if q["status"] != "OK"), None)
        code = auto_failure_code(success=success, status=ans.status, task_type=task.task_type,
                                 last_error_class=last_error,
                                 pred_empty=bool(executed and not rec.result.rows),
                                 gold_empty=not gold.result.rows, tables_exact=tables_exact, abstained=abstained,
                                 numeric_claims_on_insufficient=task.task_type == "insufficient" and bool(numeric),
                                 ungrounded_claims=bool(bad))
        retrieval_recall = None
        if pred.retrieved_tables is not None and task.result_checkable:
            gt = tables_in(task.gold["sql"], dialect)
            if gt:
                retrieval_recall = len(gt & {t.lower() for t in pred.retrieved_tables}) / len(gt)
        u = pred.usage
        score = TaskScore(task.task_id, pred.system_id, run_index, task.dataset, task.task_type, ans.status,
                         task.result_checkable, executed, correct, success, detail, had_failure, recovered_exec,
                         recovered_success, tables_exact, tables_p, tables_r, pred.counters.tool_calls,
                         pred.counters.llm_calls, pred.latency_s, pred.llm_latency_s, pred.db_latency_s,
                         u.input_tokens, u.output_tokens, u.cache_read_input_tokens, u.cache_creation_input_tokens,
                         u.cost_usd, u.uncached_cost_usd, validation_failed, checks, len(numeric),
                         len(numeric) - len(bad), abstained, silent_wrong, self_service, code, set_f1)
        score.retrieval_recall = retrieval_recall
        return score
