# Benchmark — Datasets, Evaluation Protocol, Metrics

> **Status:** SPECIFIED (Phase 0). Spec version 0.1.0, 2026-11-23. No dataset has been downloaded, split, or scored. Dataset sizes quoted for BIRD come from its publication and must be re-verified against the downloaded files (Phase 2 manifest check).

## 1. Datasets

| Dataset | Role | Engine | Why |
| --- | --- | --- | --- |
| **BIRD dev** (public; 1,534 questions, 11 databases, difficulty labels simple/moderate/challenging, per-question "evidence") | Standardized text-to-SQL accuracy | SQLite | Realistic, dirty-value databases. Widely reported, which makes relative comparisons interpretable |
| **RetailCo BI** ([DATA_MODEL.md](DATA_MODEL.md)) | End-to-end analytical tasks: metrics, multi-step, diagnosis, abstention | PostgreSQL 16 (primary), SQLite mirror | Tests what BIRD cannot: metric semantics, planted causes, insufficient data |

Spider is not used in the primary evaluation. It is older, heavily reported, and its databases are mostly clean. It may be added later as an exploratory sanity check; it is not required by any acceptance criterion.

**BIRD setting.** We use the BIRD dev split with evidence ("oracle knowledge") given to **all** systems identically. Reports name this setting explicitly. BIRD's official test split is hidden and not used.

## 2. Evaluation units

- **Task**: one `TaskInput` + gold.
- **Run**: one execution of one system on one task (`run_index ∈ {0,1,2}`).
- **Experiment**: a frozen config × task manifest × runs.

## 3. Splits and leakage prevention

### 3.1 BIRD (database-disjoint splits)

Splits are drawn from BIRD dev databases so that no database appears in more than one split. This prevents schema-specific prompt tuning from leaking into test.

Algorithm (`scripts/make_bird_splits.py`, Phase 2):

1. Permute the 11 BIRD dev `db_id`s with `seed = 20261123`.
2. Assign the first 5 to **test**, the next 3 to **val**, the last 3 to **dev**.
3. If any split's question pool is smaller than 1.5× its target size, or lacks any difficulty level, re-permute with `seed + 1`. Record the final seed.
4. Within each split, sample by stratified random sampling on difficulty, proportional to that split's pool. Targets: **test 150**, **val 100**, **dev 100** (dev may use all its pool questions for exploration).
5. Write `data/splits/bird.json` with task ids, db assignment, seed, and the SHA-256 of the source `dev.json`. Freeze it (Phase 2 acceptance).

### 3.2 RetailCo BI

| Split | Instance | Tasks | Use |
| --- | --- | --- | --- |
| dev | RetailCo-dev | 20 | Free iteration during development |
| val | RetailCo-dev | 20 | Hyperparameter/budget selection; run count logged |
| test | RetailCo-test | 50 | Held-out; Phase 6 only |
| study | RetailCo-test | 20 | Human time-to-answer study E5 (disjoint from test tasks) |

### 3.3 BI test composition (50 tasks)

| Type | Count |
| --- | --- |
| aggregate | 10 |
| ranking | 10 |
| trend | 10 |
| cohort | 8 |
| diagnostic | 8 (≥ 3 involving unlogged events) |
| insufficient | 4 |

**Held-out total for the initial evaluation: 150 BIRD + 50 BI = 200 test tasks.**

### 3.4 Leakage controls (all enforced or audited)

| Control | Mechanism |
| --- | --- |
| Systems never see gold | `TaskInput` contains no gold field (unit test). `agent_sql.systems` cannot import `agent_sql.eval` (import-linter). Gold SQL runs only in the evaluator |
| Retrieval index is built from schema documentation only | Index builder takes only catalog + description files; a test feeds it a manifest and asserts no question/gold text is read |
| Few-shot examples only from dev | Prompt registry tags few-shot blocks with source task ids; a test asserts every id belongs to a dev split |
| Test frozen early | `data/splits/bird.json` and `bi_dataset/tasks/test.jsonl` hashed and frozen in Phase 2, before A1 work starts. The runner refuses a test manifest whose hash differs from the frozen value |
| Test used once per frozen config | The runner logs every test-split run to `results/test_access_log.jsonl`. Phase 6 runs only configs whose hash is in `experiments/preregistration.md` |
| Different BI instance for test | Dev and test use different seeds and planted events ([DATA_MODEL.md](DATA_MODEL.md) §5) |
| Pretraining contamination (BIRD is public) | Cannot be prevented. It affects all systems equally; it is reported as a limitation and partially controlled by the fresh synthetic BI test ([RISKS.md](RISKS.md) R01) |

## 4. Scoring

### 4.1 BIRD: execution accuracy (official semantics)

- The evaluator re-executes the system's final SQL and the gold SQL on a fresh read-only connection (120 s timeout, 100,000-row cap).
- **Correct** iff `set(rows(pred)) == set(rows(gold))`, matching BIRD's official evaluation script (row order and duplicates ignored, column order significant).
- A **parity test** (Phase 2) runs our comparator and the official BIRD evaluation script on the same prediction file (≥ 300 predictions, including deliberate mismatches) and requires 100% agreement.
- Gold-label disputes: when manual failure analysis finds a gold SQL that appears wrong, the task is logged in `data/bird_gold_disputes.jsonl` with rationale. **Primary metrics always use unmodified gold.** A secondary "dispute-adjusted" figure may be reported separately and labeled.

### 4.2 RetailCo BI: answer_spec scoring

Each BI task has a machine-checkable `answer_spec`:

```json
{
  "type": "table",
  "key_columns": ["category_name"],
  "value_columns": [{"name": "revenue_change", "rel_tol": 0.005, "abs_tol": 0.01}],
  "ordered": true,
  "top_k": 3,
  "expected_status": "ANSWERED"
}
```

| Task type | Pass condition |
| --- | --- |
| aggregate / trend | Final result contains, for every gold key, a row whose mapped value columns are within tolerance. Column mapping is by value matching, not name: each gold value column maps to the first predicted numeric column within tolerance on ≥ 95% of keys. No extra keys beyond 5% of gold size |
| ranking | Top-k keys in gold order (`ordered: true`) and values within tolerance |
| cohort | Set F1 between predicted and gold key sets ≥ 0.95 (F1 also reported continuously) |
| diagnostic | Every `required_finding` satisfied **and** no contradicted claim. A finding is `{entities: [...], metric_id, period, direction: up\|down, min_abs_effect, event_id\|null}`. It is satisfied if some claim has matching entities and period, the same direction, a value within tolerance of the gold value computed by the evaluator, and, when `event_id` is non-null, an `event` entity equal to it. For unlogged events, `uncertainty.level` must be `high` |
| insufficient | `status = INSUFFICIENT_DATA` (or `ANSWERED` with the missing data named in `limitations` **and** zero numeric claims about the missing quantity) |

A **contradicted claim** is a numeric claim whose entities, metric, and period match a gold-computable figure but whose value is outside tolerance.

Tolerance: `|pred − gold| ≤ max(abs_tol, rel_tol × |gold|)`, with defaults `abs_tol = 0.01` and `rel_tol = 0.005`. Percentages compare as fractions.

## 5. Metrics

Primary metrics are M01–M12. Secondary metrics are S01–S07. Every metric is reported per system, per dataset (BIRD-test, BI-test, combined), and per run, plus the mean over runs with CIs (§6).

### M01 — SQL execution success rate

Share of tasks whose **final SQL** executed without error or timeout in the evaluator's re-execution. Tasks with no final SQL (any `FAILED_*`, or `INSUFFICIENT_DATA` with no final query) count as not executed. **Executable ≠ correct.** M01 is reported next to M02 and never in place of it.

### M02 — SQL execution accuracy

BIRD: share of tasks correct under §4.1. BI: share of tasks whose final result passes §4.2, computed over the **result-checkable subset** (`aggregate`, `trend`, `ranking`, `cohort`; 38 of the 50 BI-test tasks). Errors count as incorrect.

Denominators: on BI, M01 and M02 use the result-checkable subset; M03 uses all BI tasks. On BIRD, all metrics use all tasks.

### M03 — End-to-end task success rate

BIRD: M02-correct **and** `status = ANSWERED`. BI: passes the full §4.2 condition for its type (all six types). This is the **primary outcome** for hypotheses H1–H3.

### M04 — Agent error recovery rate

Among tasks with ≥ 1 SQL candidate failure during the run: (a) **recovery-to-execution** = share whose final SQL executes successfully; (b) **recovery-to-success** = share that end M03-successful. B0/B1 cannot recover by construction and report N/A. Also reported, paired: **rescue rate** = share of tasks where B1's single query failed to execute but A1 (or A2) reached M03 success.

### M05 — Schema/table selection accuracy

Tables referenced in the final SQL (sqlglot, CTEs excluded) vs tables in gold SQL: **exact-set match rate** (primary) and micro precision/recall/F1. For B1/A1/A2, also report retrieval recall (S03).

### M06 — Average number of tool calls

Mean and median of data-tool invocations per task (control tools excluded). B0 = 1 (execution), B1 = 2 (retrieval + execution). Also reported: LLM calls per task.

### M07 — End-to-end latency

Wall-clock seconds from `run_start` to `final`, as p50, p90, and mean, decomposed into LLM time, DB time, and controller overhead. Measured under the fixed scheduling of [EXPERIMENTS.md](EXPERIMENTS.md) §6 (fixed concurrency, interleaved systems). SDK transport retries are included and counted.

### M08 — Token consumption

Per task: input tokens (uncached), cache-read input tokens, cache-creation input tokens, and output tokens (thinking included, as billed), all from provider-reported `usage`. Mean, median, and p90.

### M09 — API cost per task

Σ over calls of `uncached_input × p_in + cache_read × p_cache_read + cache_creation × p_cache_write + output × p_out`, using `configs/pricing.yaml` at its recorded version. Also reported: an **uncached-equivalent cost** (all input at `p_in`), so caching effects are visible.

### M10 — Cost per successfully completed task

`total cost over all tasks (including failures) / number of M03-successful tasks`. Undefined (reported as ∞) when there are no successes. CI by bootstrap over tasks.

### M11 — Result validation failure rate

Share of tasks whose final answer fails ≥ 1 of the checks V1–V8 ([BASELINES.md](BASELINES.md) §A2), computed **post hoc by the evaluator for every system** with the same check code. For A2, the in-loop validation status is also reported separately. This makes the metric comparable across systems that do not validate.

### M12 — Reliability across runs

Over k = 3 runs per (system, task): mean ± SD of M03 across runs; **pass^3** (share of tasks successful in all 3 runs); **pass@3** (successful in ≥ 1 run); **flip rate** (share of tasks whose outcome is not identical across runs).

### Secondary metrics

| ID | Metric | Definition |
| --- | --- | --- |
| S01 | Grounded-claim rate | Numeric claims passing V1 / all numeric claims (BI). Measures **faithfulness to the system's own evidence** |
| S02 | Factual-consistency rate | Numeric claims whose value matches an evaluator-computed gold figure / gold-checkable claims. Measures **truth**, separately from S01 |
| S03 | Schema retrieval recall@k | Share of gold tables present in the initial retrieved context (B1/A1/A2) |
| S04 | Abstention accuracy | On `insufficient` tasks: correct abstention rate. On answerable tasks: false-abstention rate |
| S05 | Silent-wrong-answer rate | Share of tasks with `status = ANSWERED`, no FAIL/WARN validation, and M03-failed. Answers that look trustworthy but are wrong, the most damaging BI outcome |
| S06 | Self-service rate (BI proxy) | Share of BI tasks M03-successful **and** in-loop or post-hoc `validation = PASSED` |
| S07 | Explanation quality (exploratory) | LLM-judge rubric score 1–5 on diagnostic tasks, blinded to system id; reported only if the human audit in E6 reaches Cohen's κ ≥ 0.6 against human labels on ≥ 50 items |

## 6. Statistics

- **Per-run proportions:** Wilson 95% CI.
- **Means over runs and per-task paired differences:** non-parametric bootstrap over tasks (10,000 resamples, seed `20261123`), percentile 95% CI. For multi-run differences, resample tasks and average each task's per-run outcomes.
- **Paired significance (per pre-registered comparison):** exact McNemar test (binomial on discordant pairs) on the **per-task majority outcome** over 3 runs. Also reported: run-wise McNemar for each run index and the paired bootstrap Δ.
- **Multiplicity:** Holm–Bonferroni across the 3 primary hypotheses (H1–H3). Secondary and ablation comparisons are reported with unadjusted CIs and labeled exploratory.
- **Power (planning arithmetic, not a result):** assume n = 200 paired tasks with 40 discordant pairs (20%). The smallest observed split that reaches two-sided exact-McNemar p < 0.05 is 27 vs 13, a 7-percentage-point difference. Detecting a true difference with 80% power needs about 9 percentage points. Smaller true differences will often come out **inconclusive**, and reports will say so.
- **Cost and latency comparisons:** ratio of means with bootstrap CI; no hypothesis test.

## 7. Failure classification

Every M03-failed task gets one **primary** failure code (and optional secondary codes). Codes marked *auto* are assigned deterministically from traces. Others are hand-labeled in E7 using the labeling guide `docs/spec/labeling_guide.md` (written in Phase 6), with a second labeler on a 30% sample when available (κ reported).

| Code | Meaning | Assignment |
| --- | --- | --- |
| F-PARSE | Final/only SQL unparsable | auto |
| F-SAFETY | Rejected by safety policy | auto |
| F-SCHEMA-TABLE | Wrong or missing table | auto (vs gold tables) + review |
| F-SCHEMA-COLUMN | Nonexistent or wrong column | auto for nonexistent; hand for wrong |
| F-JOIN | Wrong join path or fan-out | hand (V5 hint) |
| F-FILTER | Wrong predicate or literal | hand |
| F-AGG | Wrong aggregation or grouping | hand |
| F-METRIC | Metric definition violated (e.g. gross vs net, test accounts included) | auto from V2 + hand |
| F-PERIOD | Wrong time window or basis | auto from V4 + hand |
| F-ORDER-LIMIT | Ranking/top-k error | hand |
| F-EMPTY | Empty result where gold is non-empty | auto |
| F-EXEC-TIMEOUT | Timed out | auto |
| F-EXEC-ERROR | Other execution error | auto |
| F-BUDGET | Repair, tool, token, or time budget exhausted | auto |
| F-ANSWER-UNGROUNDED | Claims not supported by evidence (V1) | auto |
| F-ANSWER-INTERP | Correct SQL, wrong conclusion in the answer | hand |
| F-ABSTAIN-WRONG | Abstained on an answerable task | auto |
| F-FABRICATION | Numbers asserted without evidence, or answered an insufficient task | auto + hand |
| F-PROVIDER | API error or refusal | auto |
| F-GOLD-DISPUTE | Gold suspected wrong (logged; not removed from primary metrics) | hand |

Reports include a failure-code × system contingency table and the paired transition table B0→A2 (which failure codes A2 fixes, introduces, or leaves).

## 8. What the benchmark cannot show

- Absolute BIRD scores may be inflated by pretraining contamination. Relative comparisons share that bias but are not guaranteed to be immune to it.
- RetailCo is synthetic. Effects on it show behavior under controlled conditions, not production value.
- One model family. Conclusions about "agentic architectures" generalize only as far as replication on other models (optional future work) supports.
