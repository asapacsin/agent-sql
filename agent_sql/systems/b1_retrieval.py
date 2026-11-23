"""B1 (schema retrieval baseline) and B1-SC (compute-matched self-consistency, ablation AB8)."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from functools import lru_cache

from agent_sql.db.executor import Database
from agent_sql.llm.base import ProviderError
from agent_sql.retrieval.schema_index import SchemaIndex
from agent_sql.systems.b0_oneshot import OneShotSystem
from agent_sql.systems.base import ModelMismatch, RunContext, task_context
from agent_sql.systems.composer import compose
from agent_sql.systems.contracts import FinalAnswer, RunResult, TaskInput

_INDEXES: dict[tuple, SchemaIndex] = {}


def schema_index(db: Database) -> SchemaIndex:
    key = (db.db_id, db.snapshot_hash, id(db.doc_catalog))
    if key not in _INDEXES:
        _INDEXES[key] = SchemaIndex.build(db)
    return _INDEXES[key]


class B1System(OneShotSystem):
    def schema_context(self, ctx: RunContext) -> str:
        idx = schema_index(self.rt.db)
        tables = idx.retrieve(ctx.task.question, ctx.task.evidence, self.cfg.b1_top_k_tables or 5)
        ctx.retrieved_tables = list(tables)
        ctx.tool_calls += 1  # retrieval counts as one (deterministic) tool call (M06)
        ctx.trace.emit("tool_call", tool="retrieve_schema", purpose="schema retrieval", args={"top_k": len(tables)})
        ctx.trace.emit("tool_result", tool="retrieve_schema", status="OK", summary=f"tables: {tables}")
        return idx.render(tables, ctx.task.question)


@lru_cache(maxsize=4096)
def _fingerprint(rows_repr: str) -> str:
    return hashlib.sha256(rows_repr.encode()).hexdigest()


class B1SelfConsistency(B1System):
    """n independent B1 generations; final = majority vote over result-set fingerprints (ties: lowest index)."""

    def run(self, task: TaskInput) -> RunResult:
        n = int(self.cfg.flags.get("samples", 5))
        ctx = RunContext(self.rt, task, self.system_id)
        ctx.trace.emit("run_start", db_id=task.db_id, model=self.rt.model.model_id, samples=n)
        context = task_context(task, self.rt.db)
        try:
            schema = self.schema_context(ctx)
            ok = []
            for i in range(n):
                # identical requests would hit the replay cache identically; the sample index varies the request
                sql = self.generate_sql(ctx, schema, context + f"\n\n(Independent attempt {i + 1} of {n}.)")
                if sql is None:
                    continue
                ctx.tool_calls += 1
                rec = ctx.execute(sql, purpose=f"candidate {i + 1}")
                if rec.ok and rec.result is not None:
                    fp = _fingerprint(json.dumps(sorted(map(repr, rec.result.rows))))
                    ok.append((i, fp, rec))
            if not ok:
                return ctx.result(FinalAnswer.failure("FAILED_EXECUTION", "no candidate executed successfully"))
            counts = Counter(fp for _, fp, _ in ok)
            best = max(counts.values())
            winner = next(rec for _, fp, rec in ok if counts[fp] == best)
            if task.db_id.startswith("retailco_") and "composer" in self.prompts:
                answer = compose(ctx, self.prompts["composer"], winner, context)
            else:
                answer = FinalAnswer(status="ANSWERED", answer_text=f"Result of {winner.query_id}",
                                     final_query_id=winner.query_id, tables=[winner.query_id])
            return ctx.result(answer)
        except ModelMismatch as e:
            return ctx.result(FinalAnswer.failure("FAILED_MODEL_MISMATCH", str(e)))
        except ProviderError as e:
            return ctx.result(FinalAnswer.failure("FAILED_PROVIDER", f"{e.kind}: {e}"))
