# E0 pilot — measured cost and latency per system (val)

**Status:** dev-only calibration on validation splits (BIRD-val + BI-val, 120 tasks, 1 run). Not a reported result; test splits untouched.

- Commit: `1149b9e` + E0 config cap commit; config `configs/experiments/e0_pilot.yaml`; output `results/e0_pilot/report.md`
- Model: `qwen3.7-plus-2026-05-26`; pricing `configs/pricing.yaml` 2026-11-24 (cache reads charged at the full input rate — upper bound, EXPERIMENTS §9.3 R2)
- 600/600 task-runs, 0 runner errors. B0–A2 requests served from the val calibration cache (R6/R7); new spend ≈ $1.6 (B1-SC)

## Measured per-task cost (combined val)

| system | M03 success | LLM calls/task | input tok/task | output tok/task | cost/task $ | cost/success $ |
| --- | --- | --- | --- | --- | --- | --- |
| B0 | 0.742 | 1.16 | 2,862 | 1,438 | 0.0034 | 0.0046 |
| B1 | 0.742 | 1.17 | 3,519 | 1,598 | 0.0040 | 0.0053 |
| B1-SC (n=4) | 0.750 | 4.17 | 12,908 | 4,977 | 0.0131 | 0.0175 |
| A1 | 0.708 | 3.24 | 19,815 | 1,658 | 0.0106 | 0.0149 |
| A2 | 0.608 | 7.11 | 45,358 | 2,798 | 0.0226 | 0.0372 |

## Projected cost of the remaining plan (measured means × task-runs, ×1.5 margin for test-split variance)

| Item | Task-runs | Projected $ (with margin) |
| --- | --- | --- |
| E1 main: B0, B1, A1, A2 × 200 × 3 | 2,400 | ~$24 (~$37) |
| E2 ablations, ~10 agent variants × 200, 1 run | ~2,000 | ~$30 (~$45) |
| E2 ablations, 3 runs | ~6,000 | ~$90 (~$135) |
| E3 robustness, 4 × 90 | 360 | ~$4 (~$6) |

| Option (h-approve-budget) | Projected $ (with margin) |
| --- | --- |
| A — E1 + E2 × 3 runs + E3 | ~$118 (~$178) |
| B — E1 + E2 × 1 run + E3 | ~$58 (~$88) |
| C — E1 + E3 only | ~$28 (~$43) |

These replace the unmeasured planning estimates in `docs/spec/work.json` (h-approve-budget: $500–4,600), which used a previous model's prices.

## Not verified

- Provider cache-read discount (costs above are upper bounds).
- Cost on the test splits; BI-test may differ from BI-val (margin applied).
- Val accuracy does not predict test results; no performance claim is made here.
