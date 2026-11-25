# Experiments — Matrix, Ablations, Reproducibility

> **Status:** SPECIFIED (Phase 0). Spec version 0.1.0, 2026-11-23. No experiment has run. Cost figures in §9 are assumption-based planning estimates and must be replaced by the E0 pilot measurement.

## 1. Experiment overview

| ID | Name | Split | Systems | Runs | Phase | Owner |
| --- | --- | --- | --- | --- | --- | --- |
| E0 | Pilot and calibration | BIRD-val + BI-val | B0, B1, A1, A2 | 1 | 6 (start) | autonomous (needs API key) |
| E1 | Main comparison | BIRD-test + BI-test (200) | B0, B1, A1, A2 | 3 | 6 | autonomous (needs approved budget) |
| E2 | Ablations | BIRD-test + BI-test (200) | AB1–AB9 variants | 3 (or 1, per budget decision) | 6 | autonomous |
| E3 | Robustness / fault injection | 30 BI-test tasks × 3 fault modes | B0, B1, A1, A2 | 1 | 6 | autonomous |
| E4 | Cross-engine check (exploratory) | BI-test on SQLite mirror | B0, A2 | 1 | 6 | autonomous |
| E5 | Analyst time-to-answer study | BI-study (20) | humans ± A2 | — | 6 | **human** (`h-run-analyst-study`) |
| E6 | LLM-judge audit for S07 | 50 diagnostic answers | — | — | 6 | **human** labels |
| E7 | Failure labeling | all M03-failed test tasks of B0 and A2 (+ sample of others) | — | — | 6 | human-assisted |

Development-time runs (dev/val) in Phases 1–5 are not experiments. They are logged but never reported as results.

## 2. Pre-registered hypotheses

Written into `experiments/preregistration.md` together with the frozen config hashes and test manifest hashes, and committed **before** E1 runs. Primary outcome: M03 (end-to-end task success), combined test set (n = 200) unless stated.

| ID | Type | Hypothesis | Comparison |
| --- | --- | --- | --- |
| H1 | primary | Tool use + execution feedback + bounded repair raises task success over one-shot retrieval | A1 vs B1 |
| H2 | primary | Planning + verification raises task success on analytical tasks | A2 vs A1, **BI-test** (n = 50; low power, see note) |
| H3 | primary | The full agent raises task success over the conventional baseline | A2 vs B0 |
| H4 | secondary | Verification reduces silent wrong answers | S05: A2 vs A1 |
| H5 | secondary | A1's gain over B1 is not explained by compute alone | A1 vs B1-SC (AB8) |
| H6 | secondary | Schema retrieval helps one-shot generation | B1 vs B0 |

Primary tests: exact McNemar on per-task majority outcomes, Holm-corrected across H1–H3 ([BENCHMARK.md](BENCHMARK.md) §6). With n = 50, H2 can detect only large effects. If inconclusive, it is reported as inconclusive, together with the combined-set A2 vs A1 result as a secondary figure.

**Every hypothesis may fail.** Each outcome is reported with its effect size and CI regardless of direction.

## 3. Main matrix (E1)

| | BIRD-test (150) | BI-test (50) |
| --- | --- | --- |
| B0 | 3 runs | 3 runs |
| B1 | 3 runs | 3 runs |
| A1 | 3 runs | 3 runs |
| A2 | 3 runs | 3 runs |

= 4 systems × 200 tasks × 3 runs = **2,400 task-runs**.

Reported per cell: M01–M12, S01–S06; per difficulty (BIRD) and per task type (BI).

## 4. Canonical configuration

Mirrors `docs/spec/parameters.json`. `scripts/check_spec.py` fails if they disagree. Phase 1 turns these into `configs/*.yaml`.

| Parameter | Value |
| --- | --- |
| `model_id` | qwen3.7-plus-2026-05-26 |
| `effort` | medium |
| `max_tokens_per_call` | 16000 |
| `statement_timeout_s` | 30 |
| `task_wall_clock_s` | 300 |
| `max_result_rows_stored` | 10000 |
| `observation_max_rows` | 50 |
| `observation_max_cell_chars` | 200 |
| `eval_reexec_timeout_s` | 120 |
| `eval_reexec_max_rows` | 100000 |
| `b1_top_k_tables` | 5 |
| `max_repair_attempts` | 3 |
| `a1_max_llm_calls` | 12 |
| `a1_max_tool_calls` | 15 |
| `a1_max_input_tokens` | 200000 |
| `a2_max_llm_calls` | 24 |
| `a2_max_tool_calls` | 30 |
| `a2_max_input_tokens` | 400000 |
| `a2_max_failures_per_task` | 6 |
| `a2_max_plan_steps` | 5 |
| `a2_max_replans` | 1 |
| `a2_max_verification_queries` | 4 |
| `final_answer_reserve_tokens` | 20000 |
| `runs_per_config` | 3 |
| `bootstrap_resamples` | 10000 |
| `seed` | 20261123 |
| `bird_test_size` | 150 |
| `bird_val_size` | 100 |
| `bi_test_size` | 50 |
| `rel_tolerance` | 0.005 |
| `abs_tolerance` | 0.01 |

Budget values may be changed **only** after E0 and before preregistration, and only if the change is recorded with its reason (e.g. "> 10% of A1 val tasks hit the tool budget"). After preregistration they are frozen.

## 5. Ablations (E2)

Each ablation changes exactly one flag ([BASELINES.md](BASELINES.md) §4) relative to its base system, so its effect is attributable.

### AB1 — Schema retrieval in one-shot

B0 (full schema) vs B1 (retrieved). Same comparison as H6. Isolates: schema retrieval without agency.

### AB2 — Schema retrieval in the agent

A1 with `schema_context=full` and A1 with `schema_context=names_only`, each vs A1 (`retrieved`). Isolates: whether retrieval helps, hurts, or is irrelevant once tools exist.

### AB3 — Schema exploration tools

A1 with `exploration_tools=off` vs A1. Isolates: `list_tables`, `describe_table`, `inspect_relationships`, `sample_rows`.

### AB4 — Execution feedback content

A1 with `execution_feedback=binary` vs A1. Isolates: whether *seeing* errors and rows matters, or whether retrying alone helps.

### AB5 — Error correction budget

A1 with `repair_budget=0` and `repair_budget=1` vs A1 (3). Reports the success-vs-repair-budget curve and marginal cost per extra success.

### AB6 — Explicit task planning

A2 with `planning=off` (verification on) vs A2. Isolates: decomposition.

### AB7 — Result verification

A2 with `verification=off` (planning on) vs A2. Isolates: INSPECT + VERIFY + grounding revision. V1–V8 are still computed post hoc, so M11 stays comparable.

### AB8 — Compute-matched control (B1-SC)

B1 self-consistency with `n` chosen in E0 so median tokens per task match A1 within ±15%. Compared with A1 (H5). Isolates: agency vs simply spending more tokens.

### AB9 — Effort level (exploratory, optional)

A2 with `effort=low` and `effort=high` vs A2 (`medium`). Run only if the budget decision allows. Never used to retune the main comparison after preregistration.

### Decision rule per capability

For each ablation, compute Δ M03 (base − ablated) with paired bootstrap 95% CI, plus Δ cost (M09) and Δ latency (M07) with CIs:

| Classification | Condition |
| --- | --- |
| **Effective** | Δ M03 CI excludes 0 (in favor of the capability) |
| **Harmful** | Δ M03 CI excludes 0 (against the capability) |
| **Cost-only** | Δ M03 CI includes 0 **and** Δ cost or Δ latency CI excludes 0 (capability costs more) |
| **Inconclusive** | Neither |

Also reported: **marginal cost per additional success** = Δ total cost / Δ successes (only when Δ successes > 0).

## 6. Run protocol

1. `agent-sql run --experiment configs/experiments/<id>.yaml` refuses to start if the git tree is dirty, a manifest hash differs from the frozen value, the config hash is not in the preregistration (test split only), or the projected cost exceeds `max_experiment_usd`.
2. Schedule: for each `run_index`, all (task, system) pairs are shuffled with seed `seed + run_index` and executed by **4 concurrent workers**, so systems are interleaved in time (balancing rate-limit and time-of-day effects).
3. Each task-run gets a fresh conversation, a fresh result store, and its own DB connection.
4. LLM calls go through `CachedLLM` in `record` mode. The cache is kept with the raw results, so any run can be re-scored or replayed at zero API cost.
5. A running cost meter stops the experiment cleanly (finishing in-flight tasks) when `max_experiment_usd` is reached, and marks the experiment `INCOMPLETE`.
6. Provider errors that exhaust SDK retries mark the task `FAILED_PROVIDER`. They are **not** re-run, unless the provider's status page shows an incident during the run window. Any such re-run is logged and reported.
7. After the runs: `agent-sql score`, then `agent-sql report`. Reports are generated, never hand-edited.

## 7. Reproducibility

| Artifact | Where | Tracked in git |
| --- | --- | --- |
| Experiment config + resolved config hash | `configs/experiments/`, run `manifest.json` | yes / results |
| Prompts (versioned) + SHA-256 | `prompts/`, manifest | yes |
| Dataset manifests + split files + hashes | `data/manifests/`, `data/splits/` | yes |
| BI generator + seeds + event files | `bi_dataset/` | yes |
| Pricing table + version | `configs/pricing.yaml` | yes |
| Raw predictions, traces, scores, LLM cache | `results/<experiment>/<run_id>/` | no (archived as a release asset) |
| Summary reports | `reports/<experiment>.md`, `.csv` | yes |
| Preregistration | `experiments/preregistration.md` | yes (committed before E1) |

Reproduction levels:

- **Re-score** (exact): `agent-sql score` on archived raw outputs reproduces `scores.jsonl` byte-for-byte.
- **Replay** (exact): `agent-sql run --replay` with the archived LLM cache reproduces predictions identically except wall-clock fields (`latency_s`, `llm_latency_s`, `db_latency_s`, `elapsed_ms`), the random `run_id`, `trace_path`, and trace timestamps `t`, which are excluded from the comparison (amendment A3, pending approval).
- **Re-run** (statistical): fresh API calls give new samples. Agreement is judged by overlapping CIs, not identical numbers. The model cannot be made deterministic (no sampling controls), and provider-side model updates under the same id are possible ([RISKS.md](RISKS.md) R04).

## 8. Reporting rules

- Report every pre-registered hypothesis, including null and negative results.
- Report M01 next to M02. Never present executable SQL as correct SQL.
- Report cost and latency with every accuracy number.
- Report failures by code (F-*) for every system.
- State what was **not** verified (e.g. "E5 not run: time-to-answer claims not verified").
- Exploratory analyses (anything not pre-registered, AB9, E4, S07) are labeled exploratory.
- No result from dev/val runs appears as a headline number.

## 9. Cost model and cost control

### 9.1 Measured cost model (val calibration, `qwen3.7-plus-2026-05-26`, CHANGELOG A22)

Measured on BIRD-val + BI-val (120 tasks, 1 run, `reports/dev/val_calibration.md`). Mean tokens per task:

| System | Mean tokens / task | LLM calls / task | Input share | Provider cache-hit share of input |
| --- | --- | --- | --- | --- |
| B0 | 4,300 | 1.2 | — | — |
| B1 | 5,100 | 1.2 | — | — |
| A1 | 22,700 | 3.5 | 92% | 72% |
| A2 | 50,300 | 7.2 | 94% | 66% |
| B1-SC (n = 4) | 17,900 | 4–5 | — | — |

**What drives agent cost.** An agent call carries a fixed prefix (system prompt, retrieved schema, tool definitions: median ~5,100 input tokens on A1's first call; tool definitions alone ~1,600 tokens). Each later step adds only the new observation (median ~270 tokens, p90 ~900; observations are capped at 50 rows and 200 characters per cell by `configs/limits.yaml`). Agent cost is therefore approximately **LLM calls × fixed prefix**. History pruning and observation truncation, the usual levers for long coding agents, save little here; the levers that matter are (a) prompt caching of the fixed prefix, (b) the number of LLM calls, and (c) the size of the experiment.

**Token budget of the plan (measured means × task-runs).**

| Scope | Task-runs | Tokens |
| --- | --- | --- |
| Val calibration = E0 pilot (§9.3 R7) | 5 systems × 120 | ~12M |
| E1 main | 4 × 200 × 3 = 2,400 | ~50M |
| E2 ablations, 1 run, 200 tasks (≈ 10 variants) | ~2,000 | ~60M |
| E2 ablations, 3 runs | ~6,000 | ~180M |
| E3 robustness | 4 × 90 = 360 | ~7M |
| **Plan with E2 at 1 run** | | **~130M** |

Cost per 1M tokens at list price, with no cache discount, ≈ 0.93 × $0.40 + 0.07 × $1.60 ≈ **$0.48** (~$62 for 130M). With a provider cache discount on ~70% of agent input, the real cost is lower; it is reported only from the confirmed cache rate (R2).

### 9.2 Incidents that set these rules

| Incident | Waste | Rule |
| --- | --- | --- |
| Two runner processes on one output directory ran every task twice (val v1) | ~$3.4 | R4 |
| Provider sent an array argument as a JSON string; strict validation rejected it and the agent resent it until the LLM-call budget (val v3: A1 12.7 vs 3.5 calls/task, A2 23.9 vs 7.2) | ~$7.9 | R3, R5 |
| Cache-read tokens billed at the full input rate in `configs/pricing.yaml` because the cache rate was not confirmed | over-reported cost | R2 |

### 9.3 Cost-control rules (binding)

- **R1 Stable cacheable prefix.** System prompt, schema context and tool definitions are identical across the calls of one task and placed before any volatile content; nothing per-call (timestamps, run ids, timing) enters a request (also required for replay, CHANGELOG A9).
- **R2 Priced cache.** `configs/pricing.yaml` records the provider's cache-read rate from the official price page or the provider's billing records, with source and date. Until confirmed, cache reads are charged at the full input rate (upper bound) and reports say so.
- **R3 No-progress guards in code.** An invalid argument repaired deterministically is logged (`ARGS_COERCED`); 3 invalid control-tool calls end the task with `FAILED_INVALID_OUTPUT`; tool, LLM-call, token and wall-clock budgets stay as in `configs/limits.yaml` (CHANGELOG A24).
- **R4 One runner per output directory** (`.runner.lock`); runs are resumable, never restarted from scratch when a checkpoint exists.
- **R5 Smoke before scale.** After any change to a system, prompt, tool schema, provider or model, a live check of **5 tasks per changed system** runs first. The full run starts only if the smoke run has 0 runner errors, no budget hit caused by repeated identical calls, and mean LLM calls per task within 1.5× the last calibrated value.
- **R6 Re-run only what changed.** Systems whose requests are unchanged are served from the record/replay cache; calibration after a change re-runs only the changed systems.
- **R7 E0 reuses val calibration.** E0 is the final val calibration run on the frozen configuration; it is not run a second time.
- **R8 Spend caps.** Every live experiment config sets `max_experiment_usd`; the runner stops at the cap. Before a run, the expected cost (§9.1 means × task-runs) is written in the run's config comment; a run whose expected cost exceeds the remaining approved budget does not start.
- **R9 Report cost honestly.** Reports give tokens, cache-hit share and cost per task and per success from the trace usage; waste from incidents is reported, not hidden.

### 9.4 Owner decisions on scope (decided 2026-11-24: option B — E1 × 3 runs, E2 × 1 run, E3; cap $90; CHANGELOG A28)

| Option | Saves | Effect |
| --- | --- | --- |
| E2 ablations on 100 of the 200 test tasks (1 run) | ~30M tokens | wider ablation CIs only; E1 unchanged |
| E2 ablations at 1 run instead of 3 | ~120M tokens | already the planned default under budget limits |
| Model change to a cheaper or free provider | price-dependent | all calibration re-runs on the new model (§4 pins one model per experiment) |

A human approves `max_experiment_usd` (work item `h-approve-budget`) after E0 reports measured cost per task.

## 10. E3 — Robustness / fault-injection suite

30 BI-test tasks (10 aggregate, 10 ranking, 10 trend), each under three fault modes:

| Mode | Injection | What it tests |
| --- | --- | --- |
| FM1 transient error | The executor wrapper raises a DB `OperationalError` on the first execution of each task-run | Recovery from transient failures (B0/B1 fail by design; agents may retry within the repair budget) |
| FM2 stale documentation | Schema context given to systems uses the **pre-drift** descriptions, while the DB instance `RetailCo-test-drift` renames 3 columns (`order_date→ordered_on`, `unit_price→sale_price`, `ship_region_id→ship_region`) | Discovery via schema tools after `UNKNOWN_COLUMN` |
| FM3 value-format drift | Status literals stored as title case ('Completed') in a drifted copy, with documentation unchanged | Use of `sample_rows` / metadata to fix filters, and empty-result diagnosis |

Metrics: M03 and M04 per fault mode, and recovery-to-success relative to the unfaulted E1 outcome on the same tasks.

## 11. E5 — Analyst time-to-answer study (human)

- ≥ 5 participants with working SQL skills; 20 BI-study tasks; within-subject, counterbalanced: each participant does 10 tasks unaided (SQL client + data model doc) and 10 by reviewing A2's output, with task assignment balanced via a Latin square.
- Measures: time to submitted answer, correctness (same scoring as §4.2 of [BENCHMARK.md](BENCHMARK.md)), and whether the participant accepted, edited, or rejected A2's output. Edits or rejections count as interventions.
- Analysis: mixed-effects model (time ~ condition + (1|participant) + (1|task)) reported with CI; correctness by condition.
- Until E5 runs, "reduced time to answer" is `NOT_VERIFIED`.
