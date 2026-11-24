# Val calibration (dev-only, not a reported result)

**Status:** dev-time calibration on the validation splits (BIRD-val 100 tasks, BI-val 20 tasks), 1 run, model
`qwen3.7-plus-2026-05-26`. Used to calibrate systems and budgets only; never cited as an experimental result.

## Runs

| run | commit | systems | notes |
| --- | --- | --- | --- |
| `results/val_calibration` | 048d49e (prompts v1) | B0, B1, A1, A2 | BIRD-val only (Postgres went down before BI-val ran). Two runner processes ran concurrently by operator error; the first prediction per (task, system) was kept, duplicates archived in `predictions.dupes.jsonl`. |
| `results/val_calibration_v2` | 2a12004 (A1/A2 prompts v2, CHANGELOG A21) | B0, B1, A1, A2 | complete: 480 predictions, 0 runner errors |

## Findings (v2 run, M03 task success, 95% Wilson CI)

| system | BIRD-val (n=100) | BI-val (n=20) | median tokens/task | cost (120 tasks) | budget hits |
| --- | --- | --- | --- | --- | --- |
| B0 | 0.74 [0.65, 0.82] | 0.75 [0.53, 0.89] | 3,717 | $0.41 | 0 |
| B1 | 0.76 [0.67, 0.83] | 0.65 [0.43, 0.82] | 4,408 | $0.48 | 0 |
| A1 | 0.44 [0.35, 0.54] | 0.60 [0.39, 0.78] | 17,789 | $1.34 | 1 tool budget |
| A2 | 0.49 [0.39, 0.59] | 0.75 [0.53, 0.89] | 41,288 | $2.84 | 1 tool budget |

1. **Extra-column effect (resolved by CHANGELOG A23, answer projection in code).** With prompts v1, A1/A2 scored 0.30/0.34 on
   BIRD-val; every sampled A1 loss against B0 was an extra id/date/score/name column in the final query, which strict
   BIRD EX counts as wrong. Prompt v2 (A21) improved this to 0.44/0.49, but on the 29 BIRD-val tasks that B0 answers
   correctly and A2 does not, the sampled A2 final queries still add the supporting columns A2 cites as evidence.
   The remaining gap is therefore mostly a final-query formatting effect, not an analysis failure.
2. **Budgets.** Tool-budget hits are rare (1/120 for each agent); no wall-clock or cost budget hits.
3. **Cost.** A2 costs about 7x B0 per task at measured rates (CHANGELOG A22).
4. **B1-SC.** A1 median is 17,789 tokens/task vs B1 4,408, so `samples = 4` is the candidate; it is being checked
   against the ±15% rule in `results/b1sc_calibration`.

## Not verified

- Results from one run only; no variance estimate (M12) on val.
- The A1/A2 numbers will change if the owner chooses a scoring or protocol change for the final query.

## v4 (after A23 answer projection and A24 cost guards) — supersedes v1–v3

Commit `a6f59de`, `configs/experiments/val_calibration.yaml` (`val_calibration_v4`), 480/480 task-runs, 0 runner errors, $4.86. Dev-only; not a reported result.

| system | M03 success (combined, n=120) | LLM calls/task | median tokens/task (incl. cache reads) | cost/task $ |
| --- | --- | --- | --- | --- |
| B0 | 0.742 | 1.16 | 3,717 | 0.0034 |
| B1 | 0.742 | 1.17 | 4,408 | 0.0040 |
| A1 | 0.708 | 3.24 | 16,731 | 0.0106 |
| A2 | 0.600 | 7.08 | 42,436 | 0.0225 |

B1-SC (samples=4): median 17,078.5 tokens vs A1 16,730.5 (ratio 1.02) — compute-matched. On val, neither agent beats the one-shot baselines; A2 has the lowest valid-failure rate (M11 0.067) but the highest silent-wrong rate (S05 0.292). Full tables: `results/val_calibration_v4/report.md`.
