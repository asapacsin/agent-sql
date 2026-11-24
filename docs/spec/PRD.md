# PRD — Agentic SQL Analyst

> **Status:** SPECIFIED (Phase 0). Spec version 0.1.0, 2026-11-23. Nothing described here has been implemented or measured. Every capability below is a requirement or a hypothesis, not a result.

## 1. Problem

Business users ask analytical questions ("Which categories declined last quarter, and why?") that need several dependent SQL queries, correct metric definitions, and a sanity check before anyone should trust the answer. Conventional LLM-to-SQL systems translate one question into one query. When that query fails, or runs but is semantically wrong, nothing notices.

This project builds and **measures** an autonomous SQL analyst. It accepts a natural-language business question, investigates the schema, writes and executes read-only SQL, diagnoses failures, validates results, and returns an evidence-backed answer.

## 2. Central hypothesis (to be tested, not assumed)

> An agent that can inspect, execute, observe, correct, and validate SQL completes analytical tasks more reliably than a single-pass LLM-to-SQL system, at a latency and cost that we will measure and report.

The project is judged by whether repeatable experiments can show **which** agentic capabilities improve SQL accuracy and business-analysis outcomes, and at what latency and cost. A convincing demo is not the bar. A null or negative result is a valid, reportable outcome (see [EXPERIMENTS.md](EXPERIMENTS.md) §8).

## 3. Users and scenarios

| Persona | Need | Scenario |
| --- | --- | --- |
| **Business user** (sales/ops manager, no SQL) | Self-service answer with numbers they can trust | "Which product categories experienced the largest revenue decline last quarter?" |
| **Analyst** (SQL-literate) | Faster first draft, an inspectable SQL trail, less routine querying | "Identify the customers with declining purchase frequency." The analyst reviews the SQL and the validation report |
| **Analytics lead** | Know when the system can be trusted | Reviews benchmark reports: task success, silent-wrong-answer rate, cost per correct task |
| **ML/agent engineer** | Attribute gains to specific capabilities | Runs ablations: does planning help, or only add latency? |

### 3.1 Representative questions (BI dataset)

| ID | Question | Task type | Why it is multi-step |
| --- | --- | --- | --- |
| Q-EX1 | Which product categories experienced the largest revenue decline last quarter? | ranking/comparison | Resolve "last quarter" and "revenue" (net); compute two periods; rank by decline |
| Q-EX2 | Why did customer retention decrease in September? | diagnostic | Define retention (MoM); confirm the drop; segment by region, category, and channel; check `business_events`; report a cause or uncertainty |
| Q-EX3 | Which regions generated the highest profit growth? | trend/growth | Gross profit needs a time-correct cost lookup; define the growth basis; handle unassigned regions |
| Q-EX4 | Identify the customers with declining purchase frequency. | cohort/list | Apply the window definition from DATA_MODEL; exclude test accounts |
| Q-EX5 | Compare revenue growth across regions and explain unusual changes. | diagnostic | Growth table plus anomaly detection plus event lookup |
| Q-EX6 | What was the ROI of our marketing spend last quarter? | insufficient data | No marketing-spend data exists. The correct behavior is to report that, not invent a number |

## 4. Goals and non-goals

### Goals

- G1. Four comparable systems (B0, B1, A1, A2; [BASELINES.md](BASELINES.md)) behind one interface, each testable on its own.
- G2. A reproducible benchmark: BIRD (standard text-to-SQL) plus a synthetic, realistic BI dataset with planted, documented business events ([BENCHMARK.md](BENCHMARK.md), [DATA_MODEL.md](DATA_MODEL.md)).
- G3. Pre-registered metrics with confidence intervals, paired comparisons, failure classification, and cost/latency accounting ([BENCHMARK.md](BENCHMARK.md)).
- G4. Ablations that isolate each capability ([EXPERIMENTS.md](EXPERIMENTS.md)).
- G5. A minimal Streamlit BI assistant with an observable action trace and a B0-vs-A2 comparison mode (§6).
- G6. Deterministic safety: read-only access, single-statement SELECT only, timeouts, row limits, and budgets, all enforced in code rather than prompts ([TOOLS.md](TOOLS.md)).

### Non-goals

- NG1. A production multi-tenant service: no auth, no SSO, no horizontal scaling.
- NG2. Write-back, dashboard authoring, scheduled reports.
- NG3. A heavy agent framework. A small deterministic orchestrator is preferred ([ARCHITECTURE.md](ARCHITECTURE.md) §3).
- NG4. Frontend polish beyond what is needed to inspect answers and traces.
- NG5. Claims of business value before the measurements in §7 have been run.
- NG6. Fine-tuning or training models.

## 5. Functional requirements

IDs are referenced by the phase contracts in `docs/spec/contracts/` and the requirement manifests in `docs/spec/requirements/`.

| ID | Requirement | Verified by |
| --- | --- | --- |
| FR-01 | Accept a natural-language question and a target database id; return a `FinalAnswer` ([ARCHITECTURE.md](ARCHITECTURE.md) §6) | unit + integration tests |
| FR-02 | All four systems implement one `System.run(TaskInput) -> RunResult` interface and can each run alone | contract tests per system |
| FR-03 | Every SQL statement passes the deterministic validator before execution; writes, DDL, multi-statement input, system-catalog access, and restricted columns are rejected | adversarial SQL corpus ([TOOLS.md](TOOLS.md) §4) |
| FR-04 | Execution uses read-only credentials, a statement timeout, and a row-fetch limit | integration tests on SQLite and PostgreSQL |
| FR-05 | Agents (A1, A2) stop within the declared budgets for repairs, tool calls, tokens, and wall-clock time. No unbounded retry exists | budget unit tests, property tests on the state machine |
| FR-06 | If no SQL execution succeeded, the answer status is a `FAILED_*` or `INSUFFICIENT_DATA` status and carries no numeric claims (no fabrication) | deterministic answer-contract check |
| FR-07 | Every numeric claim in an `ANSWERED` result references a query result cell (`query_id`, row, column) | grounding checker ([BENCHMARK.md](BENCHMARK.md) S01) |
| FR-08 | Each run emits a structured JSONL trace: LLM calls (usage, latency, served model), tool calls, observations, state transitions, budget events, and validations. No private chain-of-thought | trace schema tests |
| FR-09 | Each experiment is fully specified by a versioned config, versioned prompts, and dataset manifests. The run manifest records git SHA, config hash, prompt hashes, model id, and pricing version | manifest tests |
| FR-10 | The evaluation harness computes M01–M12 and S01–S07 from raw outputs, with confidence intervals and paired tests | harness unit tests on fixture predictions |
| FR-11 | Record/replay of LLM calls enables deterministic regression tests and free re-scoring | replay tests |
| FR-12 | The UI shows: answer, tables, charts, SQL, execution history, action trace, validation outcomes, warnings, and a B0-vs-A2 comparison | Streamlit AppTest smoke tests |

## 6. User experience

A Streamlit app ([ARCHITECTURE.md](ARCHITECTURE.md) §9). Visual polish is explicitly low priority.

**Ask page.** The user picks a database (default: BI dev instance), picks a system (default: A2), and enters a question. The answer view contains:

1. **Answer:** a short business answer (≤ 1,200 characters), a status badge (`ANSWERED`, `INSUFFICIENT_DATA`, `FAILED_*`), and a validation badge (`PASSED`, `WARNINGS`, `FAILED`, `NOT_RUN`).
2. **Key figures:** each numeric claim, linked to its evidence cell.
3. **Supporting tables:** result tables referenced by the answer.
4. **Chart:** chosen by a deterministic rule from the result's shape ([ARCHITECTURE.md](ARCHITECTURE.md) §9.2). It is never generated by free-form LLM code.
5. **Generated SQL:** the final query and every executed query, syntax-highlighted.
6. **Execution history:** each query with status, latency, row count, truncation flag, and error class.
7. **Action trace:** ordered tool actions, each with its declared purpose (a ≤ 200-character field the model fills on every tool call), arguments, an observation summary, and controller decisions (repair, budget stop, verification failure). **No private model reasoning is shown.** The API does not return raw chain-of-thought; thinking display is left at `omitted`.
8. **Validation outcomes:** each check (V1–V8, [BASELINES.md](BASELINES.md) §A2) with PASS, WARN, FAIL, or N/A, plus details.
9. **Warnings and limitations:** truncation, metric-interpretation assumptions, ambiguous periods, missing data, and model fallbacks if any were served.

**Compare page.** One question runs through B0 and A2 (any two systems can be selected). Shown side by side: answer, status, validation, latency, input/output/cached tokens, tool calls, LLM calls, and estimated cost. The two runs execute sequentially, so neither competes for rate limits with the other, and the order is randomized and shown.

**Trace page.** Load any saved run (`results/.../traces.jsonl`) and replay the trace view offline.

## 7. Business impact: what we will measure, and how

No business-impact claim may be made until the corresponding measurement has run. Until then, each is reported as `NOT_VERIFIED`.

| Claim | Metric | Method | Evidence level needed |
| --- | --- | --- | --- |
| Improved correctness of generated analyses | Task success rate (M03) on BI-test, paired vs B0/B1 | Benchmark E1 | Automated, 3 runs, CIs |
| Reliable recovery from database errors | Recovery rate (M04) and robustness-suite success (E3) | Fault-injection suite | Automated |
| Reduced analyst intervention | Intervention-required rate = 1 − validated-success rate; **silent-wrong-answer rate** (S05) | Benchmark E1, BI-test | Automated proxy; human confirmation in E5 |
| Increased self-service analytics | Self-service rate = share of BI tasks that are correct **and** `validation=PASSED` (S06) | Benchmark E1 | Automated proxy |
| Reduced time to answer | Analyst time-to-correct-answer, unaided vs reviewing A2 output | Human study E5 (≥ 5 SQL-literate participants, counterbalanced) | Human study; `HUMAN_PHYSICAL` work item |

The benchmark proxies (S05, S06) measure system behavior, not organizational outcomes. Reports must say so.

## 8. Success criteria for the project (not for the agent)

The project succeeds when:

1. All four systems run end-to-end on the frozen test split with 3 runs each, and the raw outputs reproduce the reported tables via `agent-sql report`.
2. Pre-registered hypotheses H1–H3 ([EXPERIMENTS.md](EXPERIMENTS.md) §2) each have a reported effect size, 95% CI, and an exact McNemar p-value with Holm correction, whatever the direction.
3. Each ablation reports Δ task success and Δ cost/latency with CIs and a classification: *effective*, *cost-only*, or *inconclusive*.
4. A failure taxonomy covers 100% of failed test tasks for A2 and B0 (auto-coded or hand-labeled).
5. A technical report states what was **not** verified.

## 9. Constraints

- One pinned LLM and model version for all systems in an experiment (default `qwen3.7-plus-2026-05-26`; [ARCHITECTURE.md](ARCHITECTURE.md) §5).
- Python 3.11+, SQLAlchemy 2.x, PostgreSQL 16 (BI), SQLite (BIRD and lightweight runs).
- Read-only database access everywhere.
- A per-experiment spend cap, approved by a human before Phase 6 (work item `h-approve-budget`).

## 10. Open decisions (owned by humans, tracked in `docs/spec/work.json`)

| Decision | Default if not overridden | Work item |
| --- | --- | --- |
| Provide API credentials for live runs | Live gates stay `BLOCKED_EXTERNAL`; replay/fake tests still run | `h-provide-api-key` |
| Approve the experiment spend cap | No Phase 6 test runs | `h-approve-budget` |
| Run the human time-to-answer study | Time-to-answer claims stay `NOT_VERIFIED` | `h-run-analyst-study` |
