# Failure labeling guide (E7)

> **Status:** SPECIFIED (Phase 6 asset, written before any test results exist). Used to hand-label M03-failed test tasks whose automatic code is `UNLABELED` or needs review ([BENCHMARK.md](BENCHMARK.md) section 7).

## Procedure

1. Labelers see the task question, gold SQL and its result, the system's final SQL and result, the answer, and the public action trace. They never see the system id; files are shuffled and system-blinded.
2. Assign exactly one **primary** code (the earliest error in the causal chain) and any number of secondary codes.
3. If the gold SQL appears wrong, assign `F-GOLD-DISPUTE`, write a one-paragraph rationale, and still score the task against unmodified gold (primary metrics never change).
4. A second labeler independently labels a random 30% sample; report Cohen's kappa on primary codes.
5. Disagreements are resolved by discussion; the resolution and both original labels are kept.

## Decision order for the primary code

| Step | Question | Code |
| --- | --- | --- |
| 1 | Did the provider fail or refuse? | F-PROVIDER |
| 2 | Did a budget end the run? | F-BUDGET |
| 3 | Is the final SQL unparsable / rejected by policy? | F-PARSE / F-SAFETY |
| 4 | Did execution error or time out? | F-EXEC-ERROR / F-EXEC-TIMEOUT |
| 5 | Wrong or missing table? | F-SCHEMA-TABLE |
| 6 | Nonexistent or wrong column (e.g. gross vs net amount column)? | F-SCHEMA-COLUMN |
| 7 | Wrong join path or row multiplication? | F-JOIN |
| 8 | Metric definition violated (test accounts, status filter, refunds, cost-date logic)? | F-METRIC |
| 9 | Wrong period or comparison basis? | F-PERIOD |
| 10 | Wrong filter value or predicate? | F-FILTER |
| 11 | Wrong aggregation or grouping? | F-AGG |
| 12 | Correct set, wrong ranking / top-k? | F-ORDER-LIMIT |
| 13 | Empty result where gold is non-empty? | F-EMPTY |
| 14 | SQL correct but the answer text misreads it? | F-ANSWER-INTERP |
| 15 | Numbers not supported by any executed result? | F-ANSWER-UNGROUNDED |
| 16 | Abstained on an answerable task? | F-ABSTAIN-WRONG |
| 17 | Asserted numbers on an insufficient-data task? | F-FABRICATION |
| 18 | Gold appears wrong | F-GOLD-DISPUTE |

## Output

`results/<experiment>/labels.jsonl`, one row per labeled task-run: `{task_id, run_index, system_id, labeler, primary, secondary[], rationale, gold_dispute}`. Reports join labels to scores. Unlabeled failures stay `UNLABELED` and are reported as such.
