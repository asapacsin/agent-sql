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

BIRD_EX_MAX_ROWS = 5_000_000  # complete-result comparison for official EX semantics
POSTHOC_CHECKS_VERSION = "v2:V1-V8 (V3,V4 N/A post hoc)"


def tables_in(sql: str, dialect: str) -> set[str]:
    try:
        tree = sqlglot.parse_one(sql, read="postgres" if dialect == "postgres" else "sqlite")
    except Exception:  # noqa: BLE001
        return set()
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    return {t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes}


_DEV_VAL: set[str] | None = None


def _dev_val_questions() -> set[str]:
    global _DEV_VAL
    if _DEV_VAL is None:
        from agent_sql.eval.datasets.bi import load_bi_tasks

        _DEV_VAL = {t.question.strip().lower() for split in ("dev", "val") for t in load_bi_tasks(split)}
    return _DEV_VAL


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
    overlaps_dev: bool = False  # held-out BI task whose question also appears in dev/val (review finding A12)

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

    def _exec_uncapped(self, db_id: str, sql: str, validate: bool = True):  # noqa: ANN202
        key = (db_id, "UNCAPPED:" + str(validate) + ":" + sql)
        if key not in self._cache:
            ex = Executor(self.dbs[db_id], statement_timeout_s=self.timeout_s, max_rows=BIRD_EX_MAX_ROWS,
                          validate=validate)
            self._cache[key] = ex.run(sql, ResultStore())
        return self._cache[key]

    def _gold(self, task: EvalTask):  # noqa: ANN202
        from agent_sql.db.registry import gold_db_id, open_database

        gid = gold_db_id(task.db_id)
        if gid not in self.dbs:
            self.dbs[gid] = open_database(gid)
        ex = Executor(self.dbs[gid], statement_timeout_s=self.timeout_s, max_rows=self.max_rows,
                      validate=False)  # gold is trusted evaluator SQL, still on the read-only connection
        key = (gid, "GOLD:" + task.gold["sql"])
        if key not in self._cache:
            self._cache[key] = ex.run(task.gold["sql"], ResultStore())
        return self._cache[key]

    def _events(self, db_id: str) -> list[dict]:
        from agent_sql.db.registry import gold_db_id

        db_id = gold_db_id(db_id)
        if db_id not in self.dbs:
            from agent_sql.db.registry import open_database

            self.dbs[db_id] = open_database(db_id)
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
            # official EX compares complete result sets: re-execute both uncapped (BIRD_EX_MAX_ROWS) and never
            # compare truncated prefixes (reviewer finding on bird-384/bird-406).
            if executed:
                prec = self._exec_uncapped(task.db_id, final_sql)
                grec = self._exec_uncapped(task.db_id, task.gold["sql"], validate=False)
                if grec is None or not grec.ok:
                    raise RuntimeError(f"gold SQL failed uncapped for {task.task_id}")
                if grec.result.truncated:
                    raise RuntimeError(f"{task.task_id}: GOLD exceeds BIRD_EX_MAX_ROWS; refusing a prefix comparison")
                executed = bool(prec is not None and prec.ok)
                over_cap = executed and prec.result.truncated  # gold fits, so an over-cap prediction cannot match
                correct = executed and not over_cap and bird_execution_match(prec.result.rows, grec.result.rows)
                detail = "prediction exceeds BIRD_EX_MAX_ROWS (scored as mismatch)" if over_cap else ""
            else:
                correct = False
            success = bool(correct) and ans.status == "ANSWERED"
            detail = detail or ("EX match" if correct else "EX mismatch")
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
        # post-hoc validation V1-V8: the SAME check code for every system (M11). Plan periods are not part of the
        # prediction, so V3/V4 report N/A post hoc; A2's in-loop report is kept separately in answer.validation.
        from agent_sql.verification.checks import CheckInput, QueryView, run_checks

        views = {}
        for q in pred.queries:
            if q["status"] == "OK":
                r = self._exec(task.db_id, q["sql"])
                views[q["query_id"]] = QueryView(q["query_id"], q["sql"], "OK" if r.ok else r.status,
                                                 r.result.columns if r.ok else [], r.result.rows if r.ok else [],
                                                 bool(r.ok and r.result.truncated))
            else:
                views[q["query_id"]] = QueryView(q["query_id"], q["sql"], q["status"])
        report = run_checks(CheckInput(ans, views, dialect, task.dataset == "bi"))
        checks = [c.model_dump() for c in report.checks]
        from agent_sql.verification.grounding import check_grounding

        def lookup(qid: str, row: int, col: str):  # noqa: ANN202
            v = views.get(qid)
            if not v or v.status != "OK" or col not in v.columns or not 0 <= row < len(v.rows):
                return False, None
            return True, v.rows[row][v.columns.index(col)]

        _, bad = check_grounding(ans.claims, lookup)
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
        score.overlaps_dev = task.dataset == "bi" and task.split in ("test", "study") and \
            task.question.strip().lower() in _dev_val_questions()
        return score
