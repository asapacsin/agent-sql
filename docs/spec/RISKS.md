# Risks, Limitations, Mitigations

> **Status:** SPECIFIED (Phase 0). Spec version 0.1.0, 2026-11-23. Likelihood/impact ratings are judgments made before any implementation and must be revisited after E0.

Ratings: L = likelihood, I = impact (H/M/L).

## 1. Validity risks (could make conclusions wrong)

| ID | Risk | L | I | Mitigation | Residual / how reported |
| --- | --- | --- | --- | --- | --- |
| R01 | **Pretraining contamination.** BIRD is public and may be in the model's training data | H | M | Database-disjoint splits; fresh synthetic BI test instance with unpublished-at-training-time events; compare relative, not absolute, scores | Absolute BIRD numbers labeled as possibly inflated |
| R02 | **Gold-label noise in BIRD.** Some gold SQL is wrong or ambiguous | H | M | Dispute log; primary metrics on unmodified gold; separate dispute-adjusted figure; F-GOLD-DISPUTE code | Reported with counts |
| R03 | **Non-determinism.** The pinned API rejects `temperature`/`top_p`/`top_k`, so outputs vary run-to-run | H | M | 3 runs per config; M12 reliability metrics; majority-outcome McNemar; record/replay cache for exact re-scoring | Run-to-run SD reported |
| R04 | **Provider-side model change** under the same id, or fallback routing | M | H | Pin id; record `served_model` per call; benchmark mode disables fallbacks and fails tasks on mismatch; run all systems of an experiment in one interleaved window | Run window dates reported |
| R05 | **Author bias.** The same people write tasks, gold, and agent | H | H | Test tasks and BI test events authored and hash-frozen in Phase 2, before A1/A2 development; separate test DB instance; preregistration; process rule not to open test files in Phases 3–5 | Stated as a limitation; ideally an independent author writes test tasks (`h-independent-test-author`, optional) |
| R06 | **Overfitting to validation.** Repeated val runs tune prompts to val | M | M | Val run count logged; budgets frozen after E0; test runs only for preregistered configs | Val run count reported |
| R07 | **Comparator bugs** create false passes or fails | M | H | BIRD parity test against the official script (100% agreement); unit tests per answer_spec type; manual audit of 30 random judgments per system in E7 | Audit disagreement rate reported |
| R08 | **Insufficient statistical power** (n = 200; BI-only n = 50) | H | M | Pre-computed detectable effect (~9 pp at 80% power); CIs everywhere; "inconclusive" category | Inconclusive results reported as such |
| R09 | **Synthetic-data unrealism.** RetailCo effects may not reflect real BI workloads | M | M | Realistic traps (T1–T8), logged and unlogged events, documented semantics | Limitation stated; BI results not generalized to production |
| R10 | **LLM-judge bias** (S07, same model family) | H | L | S07 secondary and exploratory only; blinded; human audit κ ≥ 0.6 required to report | Not reported if κ < 0.6 |
| R11 | **Compute confound.** Agents win simply by spending more tokens | M | H | AB8 compute-matched B1-SC; cost per success (M10) | H5 reported |
| R12 | **Answer-format sensitivity.** Value-based column mapping may mis-score unusual but correct outputs | M | M | Unit tests with adversarial formats; manual audit sample | Audit reported |

## 2. Engineering and safety risks

| ID | Risk | L | I | Mitigation |
| --- | --- | --- | --- | --- |
| R13 | **SQL safety bypass** (multi-statement, function side effects, catalog reads) | L | H | Layered defense L1–L5 ([TOOLS.md](TOOLS.md) §4.1); adversarial corpus ≥ 40 cases; test that L2/L4 still block with L1 disabled |
| R14 | **Restricted-data exposure** (email/phone) | L | H | Validator SR8 + Postgres column grants + SQLite authorizer column check; corpus cases via `*`, aliases, JSON functions |
| R15 | **Runaway cost** | M | H | Per-task token/LLM/tool budgets; per-experiment `max_experiment_usd` with a live cost meter; E0 pilot before the budget is approved |
| R16 | **Latency measurement noise** (network, 429 retries, concurrency) | H | M | Fixed concurrency; interleaved schedule; SDK retry counts logged; report p50/p90 and LLM vs DB time decomposition |
| R17 | **Retrieval-index leakage** (questions/gold in index) | L | H | Index built only from schema docs; leakage unit test |
| R18 | **SQLite vs PostgreSQL dialect divergence** on BI gold | M | M | Gold parity test across engines; BI primary on Postgres; E4 exploratory only |
| R19 | **Over-engineering the orchestrator** | M | M | No framework; state machine kept small and unit-tested; new components need an ADR |
| R20 | **Provider refusals** (safety-classifier false positives on benign SQL work) | L | M | Recorded as `FAILED_PROVIDER` / F-PROVIDER; rate reported per system; fallbacks disabled in benchmark mode for fairness |
| R21 | **Prompt injection via data values** (trap T8, BIRD text columns) | M | M | Observations framed as data; deterministic enforcement means a followed injection cannot write or exfiltrate; dedicated tests with injection strings in `products.description` |
| R22 | **Context growth in long A2 runs** (cumulative input tokens) | M | M | Input-token cap; observation size caps; result store referenced by `query_id` instead of resending rows; context editing is a later optimization, not in v1 |
| R23 | **Thinking cannot be disabled; effort default is `medium`** on the pinned model | H | L | Effort set explicitly and identically for every system; AB9 explores effort; thinking blocks never displayed |
| R24 | **Credential handling in a public repo** | M | H | Keys only via environment / secret store; trace redaction; secret scanning in CI; PR template check |

## 3. Claim risks (how this project could mislead)

| ID | Risk | Mitigation |
| --- | --- | --- |
| R25 | Claiming business value from benchmark proxies | PRD §7: each claim mapped to a measurement; S05/S06 labeled as proxies; E5 needed for time-to-answer |
| R26 | Presenting executable SQL as correct | M01 always next to M02; README states the distinction |
| R27 | Cherry-picking demos | UI comparison mode is for inspection; reports use only the frozen benchmark |
| R28 | Silent retries or fabricated answers | Bounded state machine; answer contract forbids claims without successful evidence; FR-05/FR-06 tests |

## 4. Known limitations (accepted)

1. One model family in v1. Conclusions are conditional on `qwen3.7-plus-2026-05-26`.
2. English questions only.
3. No real users in the benchmark; human evaluation is limited to E5/E6/E7.
4. No production concerns (multi-tenancy, auth, SLAs).
5. BIRD with evidence is an easier setting than BIRD without evidence. Results do not transfer to the no-evidence setting.
