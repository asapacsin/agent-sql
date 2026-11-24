# Preregistration — agent-sql evaluation (E1–E4)

**Status:** locked before any test-split run. Written 2026-11-24, before E1. Any later change to a locked config produces a new hash and a new, separately labelled run; it never replaces these.

Model `qwen3.7-plus-2026-05-26` (one model for all experiments). Budget: owner-approved option B (`h-approve-budget`, 2026-11-24): E1 × 3 runs, E2 ablations × 1 run, E3; total cap $90 (`reports/e0_pilot.md`).

## Hypotheses (from [EXPERIMENTS.md §2](../docs/spec/EXPERIMENTS.md#2-pre-registered-hypotheses))

Primary outcome M03 (end-to-end task success), combined test set (n = 200) unless stated.

| ID | Type | Hypothesis | Comparison |
| --- | --- | --- | --- |
| H1 | primary | Tool use + execution feedback + bounded repair raises task success over one-shot retrieval | A1 vs B1 |
| H2 | primary | Planning + verification raises task success on analytical tasks | A2 vs A1, BI-test (n = 50; low power) |
| H3 | primary | The full agent raises task success over the conventional baseline | A2 vs B0 |
| H4 | secondary | Verification reduces silent wrong answers | S05: A2 vs A1 |
| H5 | secondary | A1's gain over B1 is not explained by compute alone | A1 vs B1-SC (AB8) |
| H6 | secondary | Schema retrieval helps one-shot generation | B1 vs B0 |

Analysis: exact McNemar on per-task majority outcomes (3 runs), Holm-corrected across H1–H3 (BENCHMARK.md §6). Secondary hypotheses are reported with effect size and 95% CI, uncorrected and labelled as such. Every outcome is reported regardless of direction.

**Disclosed before test.** Val calibration (`reports/dev/val_calibration.md`, `reports/e0_pilot.md`) showed B0 0.742, B1 0.742, A1 0.708, A2 0.608 M03 on val. The hypotheses are kept as written in the spec; this preregistration does not change their direction after seeing val.

## Test manifests

- bird:test — `e295431c537f950f859fb14a6972da1936874f6b40544adbcafa1add87204457`
- bi:test — `ce18594d391b1cd6ef14f3129c164882658eb3ff2b60e1ed98497278f51eafef`

## Locked configs

E1 main (`configs/experiments/e1_main.yaml`, B0/B1/A1/A2 × 200 × 3)
- config_hash: `d7df2f6aae6fb293cf7c4250d7d5c0959ff3dacfe6970ed928ca7e2d60d06611`

E2 ablations (`configs/experiments/e2_ablations.yaml`, AB2–AB8, 1 run)
- config_hash: `3ac701a0ac046f11839ab03456b51ed147a7d9765c1bfd3526b38cecb5f823a0`

E3 robustness (`configs/experiments/e3_fm{1,2,3}.yaml`)
- config_hash: `66f83b4b6f4a330c021396b32b4d1411509624f1737fe92ccc04c81116ed7342`
- config_hash: `9b916aa3a16e0222176ef599dc4de4019caae3baa08aa96b7ddc282985aa4de1`
- config_hash: `be12e65cec16e6fe7e2fc565861d5636fef81310d3995129fce470c5df242ffc`

E4 cross-engine, exploratory (`configs/experiments/e4_cross_engine.yaml`)
- config_hash: `073fd9cb17699867d4022ed417f9e527a7bf50825ba38b67bc2a40654aefcf43`

Hashes computed with `python3 -m agent_sql prereg --hash <config>`, which uses the runner's own resolution (configs, system configs, model, limits, prompt SHA-256, dataset manifests).
