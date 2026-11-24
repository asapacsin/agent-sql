# Baselines and Agent Architectures — B0 · B1 · A1 · A2

> **Status:** SPECIFIED (Phase 0). Spec version 0.1.0, 2026-11-23. No system is implemented and no result is known. Expected orderings are hypotheses ([EXPERIMENTS.md](EXPERIMENTS.md) §2).

## 1. What is held constant (fairness contract)

All four systems, in every experiment, share:

| Dimension | Value |
| --- | --- |
| LLM | One pinned model id per experiment (default `qwen3.7-plus-2026-05-26`); served model verified per call; fallbacks disabled |
| Inference settings | Same `effort` (default `medium`), adaptive thinking, `max_tokens=16000` per call, prompt caching on; no sampling parameters (unsupported by the API) |
| Databases | Same database files / Postgres snapshot, same `agent_ro` permissions |
| Questions and splits | Same frozen task manifests ([BENCHMARK.md](BENCHMARK.md) §3) |
| Task input | Same `TaskInput` (question, db_id, BIRD evidence, BI `as_of_date`) |
| Metric glossary | Same natural-language metric definitions ([DATA_MODEL.md](DATA_MODEL.md) §4) in context for BI tasks |
| SQL safety | Same validator, executor, statement timeout (30 s), stored-row cap (10,000), observation cap (50 rows) |
| Wall-clock cap | 300 s per task |
| Answer contract | Same `FinalAnswer` schema ([ARCHITECTURE.md](ARCHITECTURE.md) §6) |
| Scoring | Same evaluator, which re-executes final SQL independently ([BENCHMARK.md](BENCHMARK.md) §4) |

## 2. What differs (explicitly)

| | **B0** One-shot | **B1** Schema retrieval | **A1** Executable agent | **A2** Planning + verification |
| --- | --- | --- | --- | --- |
| Initial schema context | Full schema: all tables, columns, types, PK/FK, descriptions | Retrieved: top-5 tables + 1-hop FK neighbors, all their columns, ≤ 3 sample values per text column | Same retrieved context as B1 | Same retrieved context as B1 |
| LLM-callable tools | none | none | `list_tables`, `describe_table`, `inspect_relationships`, `sample_rows`, `validate_sql`, `execute_readonly_sql`, `get_query_result_metadata`, `submit_answer` | A1 tools + `compare_query_results`, `submit_plan`, `complete_step` |
| SQL generations | 1 | 1 | many (bounded) | many (bounded) |
| Executions | 1 | 1 | ≤ tool budget | ≤ tool budget |
| Sees execution results/errors | no (pipeline returns them) | no | yes | yes |
| Repair attempts | 0 | 0 | ≤ 3 | ≤ 3 per step, ≤ 6 failures per task |
| Explicit plan | no | no | no | yes (1–5 steps, ≤ 1 re-plan) |
| Deterministic intermediate inspection | no | no | no | yes (INSPECT) |
| Verification checks in loop | no | no | answer-contract check only | V1–V8 + ≤ 4 verification queries + 1 grounding revision |
| Answer composition (BI) | 1 composer call over the single result | same as B0 | model calls `submit_answer` | model calls `submit_answer`, then grounding check |
| LLM-call budget | 1 (+1 composer) | 1 (+1 composer) | 12 | 24 |
| Tool-call budget | 1 execution | 1 retrieval + 1 execution | 15 | 30 |
| Cumulative input-token cap | — (single call) | — | 200,000 | 400,000 |
| Final-answer reserve | — | — | 20,000 tokens | 20,000 tokens |

Budgets are canonical in [EXPERIMENTS.md](EXPERIMENTS.md) §4 and `docs/spec/parameters.json`. Changing one creates a new config version.

**Why A1 and A2 start from retrieved rather than full schema:** so that A1 vs B1 isolates *tools + execution feedback + repair*, and A2 vs A1 isolates *planning + verification*. Whether retrieval itself helps or hurts the agent is measured separately (ablation AB2).

**Compute is not held constant.** Agents spend more tokens by design. To separate *agency* from *more compute*, ablation AB8 (B1-SC: self-consistency at a matched token budget) is a required comparison ([EXPERIMENTS.md](EXPERIMENTS.md) §5).

## 3. System definitions

### B0 — One-shot SQL baseline

1. Receive `TaskInput`.
2. Render prompt `prompts/b0/sql.v1.md` with the full schema description (and the metric glossary for BI).
3. One LLM call with structured output `{ "sql": string }`. No tools.
4. `validate_sql` (deterministic, not LLM-callable). `REJECTED` → `FAILED_EXECUTION`.
5. Execute once. Error or timeout → `FAILED_EXECUTION`.
6. BIRD: `FinalAnswer{status: ANSWERED, final_query_id}`. BI: one composer call (`prompts/shared/composer.v1.md`) turns the single result into a `FinalAnswer`. Claims may cite only that result.
7. Return. No retry of any kind. SDK transport retries are not counted as retries; they are logged.

### B1 — Schema retrieval baseline

Identical to B0, except step 2 uses retrieved context:

- **Index** (built offline per database from schema documentation only, never from questions or gold SQL): one document per table (name, description, column names, types, and descriptions) and one per column.
- **Retriever:** BM25 over table and column documents (`rank_bm25`, pinned), plus a value index. Question n-grams are matched exactly or case-folded against distinct values of text columns with ≤ 10,000 distinct values, and each hit adds its table. Score = BM25 rank fusion with value hits. Take the top-5 tables, then add 1-hop FK neighbors (deduplicated).
- Deterministic: same input → same context. Retrieval recall is measured separately (S03).
- Prompt `prompts/b1/sql.v1.md`.

**Final-query column rule (CHANGELOG A21):** all systems are told to return the columns the question needs. A1/A2 prompts v2 add an explicit "nothing else (no extra id, date, score or context columns)" clause, because agents run exploratory queries whose context columns otherwise leak into the final query; B0/B1 write a single query and keep the shorter wording. This wording difference is a disclosed asymmetry.

### A1 — Executable SQL Agent

Bounded tool-calling loop ([ARCHITECTURE.md](ARCHITECTURE.md) §4.2). Prompt `prompts/a1/system.v1.md`.

Workflow the prompt asks for (the controller enforces only the bounds):

1. Interpret the question; state the metric and period interpretation in `submit_answer.metric_interpretations`.
2. Inspect schema with `list_tables`, `describe_table`, `inspect_relationships`, `sample_rows` as needed.
3. Identify the relevant tables and join path.
4. Write SQL. Optionally dry-run it with `validate_sql`.
5. Execute with `execute_readonly_sql`. The controller re-validates every statement, so the LLM cannot skip validation.
6. Read the observation: rows, metadata, error class, and message.
7. On a failure, repair, at most 3 repair attempts ([ARCHITECTURE.md](ARCHITECTURE.md) §4.1).
8. Submit a grounded answer with `submit_answer`. `final_query_id` must reference a successful execution.

Observation formatting for errors: `{status, error_class, message (DB message, ≤ 500 chars), hint}`. Here `error_class` ∈ `PARSE_ERROR | POLICY_VIOLATION | UNKNOWN_TABLE | UNKNOWN_COLUMN | AMBIGUOUS_COLUMN | TYPE_ERROR | TIMEOUT | ROW_LIMIT | DB_ERROR`, and `hint` is a deterministic suggestion, e.g. the closest column names by edit distance for `UNKNOWN_COLUMN`.

### A2 — Planning and Verification Agent (primary proposed architecture)

A1, plus ([ARCHITECTURE.md](ARCHITECTURE.md) §4.3). Prompts `prompts/a2/plan.v1.md`, `prompts/a2/system.v1.md`.

1. **Explicit task decomposition.** `submit_plan` takes `{steps: [{step_id, goal, expected_output: scalar|table|list, metric_ids[], depends_on[]}], periods: [{label, start, end}], assumptions[]}`, with 1–5 steps. The deterministic `PLAN_CHECK` validates step count, metric ids against the registry, a dependency DAG, and resolvable periods.
2. **Multi-step SQL analysis.** Each step runs an A1-style sub-loop with a per-step repair budget of 3. A step completes when the model calls the control tool `complete_step` ([TOOLS.md](TOOLS.md) §3) referencing a successful `query_id`, or declaring the step unanswerable with a reason.
3. **Intermediate result inspection** (deterministic INSPECT; findings returned as observations).
4. **Business-metric definition checks** (V2).
5. **Result consistency verification** (V3, V5, V8).
6. **Additional queries when justified.** Allowed only (a) as plan steps, or (b) as ≤ 4 verification queries after a failed or warning check. Each needs a `purpose`. The budget is enforced.
7. **Evidence-backed explanation.** Every numeric claim carries an `EvidenceRef`; V1 checks it.
8. **Uncertainty reporting.** `uncertainty.level` must be `high`, with reasons, when any of these hold: a required metric's data is missing, a check FAILED, the result was truncated, or a diagnostic cause is not supported by a `business_events` row or a quantified segment effect. The controller enforces the floor: it overrides a lower level and records the override as a validation event.

#### Verification checks (deterministic; results in `FinalAnswer.validation`)

| ID | Check | Applies to | FAIL condition | WARN condition |
| --- | --- | --- | --- | --- |
| V1 | **Grounding.** Each numeric claim's value equals its evidence cell (relative tolerance 0.5%, or exact after rounding to the claim's displayed precision) | all | any mismatch or missing evidence | — |
| V2 | **Metric conformance.** SQL for each `metric_id` satisfies the registry's `required_tables` and `required_predicates` (sqlglot AST match), e.g. net revenue excludes cancelled orders and test accounts | BI | missing required predicate | non-canonical but equivalent form detected |
| V3 | **Reconciliation.** When a breakdown is reported, the sum of parts equals the canonical total from the registry template for the same period | BI | relative diff > 0.5% | unassigned bucket > 5% of total |
| V4 | **Period conformance.** Date predicates in the SQL match the plan's resolved periods | BI | mismatch | half-open vs closed boundary ambiguity |
| V5 | **Fan-out / duplication.** Declared grain key is unique in the final result | all | duplicates | — |
| V6 | **Non-empty and null sanity** | all | empty final result while status = `ANSWERED` | value column > 50% null |
| V7 | **Truncation.** Final result not truncated, or truncation stated in `limitations` | all | truncated and not disclosed | truncated and disclosed |
| V8 | **Cross-query consistency.** The same metric for the same entity and period in two queries agrees (`compare_query_results`) | all | relative diff > 0.5% | — |

Not-applicable checks report `N/A`. For BIRD tasks, V2–V4 are `N/A`. Validation status: `FAILED` if any FAIL, else `WARNINGS` if any WARN, else `PASSED`.

**Leakage guard:** V2/V3 templates in `metrics.yaml` are written from [DATA_MODEL.md](DATA_MODEL.md) against the **dev** BI instance. They compute generic period totals, never task-specific answers, and are never derived from gold SQL. The verifier's SQL templates are software inputs, not LLM context.

## 4. Ablation feature flags

A1/A2 read these from config. Defaults reproduce A1/A2 exactly.

| Flag | Values | Default A1 | Default A2 |
| --- | --- | --- | --- |
| `schema_context` | `full` · `retrieved` · `names_only` | `retrieved` | `retrieved` |
| `exploration_tools` | `on` · `off` (removes `list_tables`, `describe_table`, `inspect_relationships`, `sample_rows`) | `on` | `on` |
| `execution_feedback` | `full` · `binary` (observation reduced to `{status}`; no rows, no message) | `full` | `full` |
| `repair_budget` | `0` · `1` · `3` | `3` | `3` |
| `planning` | `on` · `off` | `off` | `on` |
| `verification` | `on` · `off` (skips INSPECT, VERIFY, and GROUNDING revision; V1–V8 still computed post hoc by the evaluator) | `off` | `on` |

B1-SC (AB8) is a separate pipeline: `n` independent B1 generations, each validated and executed; final = majority vote over result-set fingerprints (ties broken by lowest candidate index); one composer call for BI. `n` is chosen on the validation split so that the median input+output tokens per task match A1's within ±15%. The chosen `n` is recorded in the config.

## 5. Prompts (versioned)

| File | Used by | Contents (summary) |
| --- | --- | --- |
| `prompts/b0/sql.v1.md` | B0 | role, dialect, full schema, glossary (BI), BIRD evidence, output schema |
| `prompts/b1/sql.v1.md` | B1, B1-SC | as B0, with retrieved context |
| `prompts/shared/composer.v1.md` | B0, B1, B1-SC | summarize one result into `FinalAnswer`; claims must cite cells; never add numbers not in the result |
| `prompts/a1/system.v1.md` | A1 | workflow, tool usage guidance, answer contract, uncertainty rules |
| `prompts/a2/plan.v1.md` | A2 | decomposition instructions, plan schema, metric registry ids |
| `prompts/a2/system.v1.md` | A2 | A1 rules + step protocol + verification guidance |

Prompts treat database values as untrusted data. They do not tell the model to follow instructions found in cells. Enforcement does not depend on this: even if injected text is followed, the validator and the read-only role prevent harmful effects ([RISKS.md](RISKS.md) R21).

Few-shot examples, if used, come **only** from the dev split and are identical across the systems that use them.
