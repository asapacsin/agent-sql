# Specification changelog

> **Status:** amendments below are PENDING OWNER APPROVAL (work item `h-approve-spec-amendments`). The approved baseline is spec v0.1.0 (commit c09c109, approved 2026-11-23). An independent review of Phase 1 flagged that these were made during execution without prior approval; they are recorded here instead of being applied silently.

## v0.1.1 (proposed during Phase 1–2 execution)

| # | Change | Where | Why | Effect on benchmark |
| --- | --- | --- | --- | --- |
| A1 | Planted event D1 description corrected: effect is on Outdoor Gear/West revenue, not a West retention dip | DATA_MODEL.md section 5.1 | Measured on generated data: retention dip was not detectable (0.593 vs 0.601) | Description only; event unchanged |
| A2 | Planted event D3 changed from `small_business` via `paid_social` x2.5 to all `small_business` x4 | DATA_MODEL.md section 5.1, `bi_dataset/events/dev.json` | Original cohort (~50 customers) made the effect statistically invisible | Dev instance data changed (dev/val only; test instance unaffected) |
| A3 | Replay criterion: identical except wall-clock fields, `run_id`, `trace_path` and trace timestamps `t` | EXPERIMENTS.md section 7, IMPLEMENTATION_PLAN.md section 3 | Wall-clock fields cannot be byte-identical across runs | Weaker wording; comparison fields listed explicitly |
| A4 | CI workflow extended with a Phase 1 job (Postgres service, gates) | `.github/workflows/ci.yml` (a forbidden path in phase-1 contract) | IMPLEMENTATION_PLAN p1-scaffold asks for "CI jobs extended"; contract forbids workflow edits | Contradiction between plan and contract; owner must pick |
| A5 | `customers.email`/`phone` nullable; agent-facing SQLite copy stores NULL in restricted columns; FK/date indexes added | `bi_dataset/schema.sql`, `bi_dataset/generate.py` | Reviewer found SQLite authorizer bypass via JOIN USING/NATURAL; indexes make data checks tractable | Defense in depth; no metric changes |
| A6 | Diagnostic findings: `require_value`, `period`, `min_abs_effect` are optional per finding; evaluator injects `gold_value` when `require_value` is true. Current tasks check entities, direction, event id, uncertainty and contradictions, not exact magnitudes | BENCHMARK.md section 4.2 (clarification) | An analyst-chosen comparison window makes exact magnitudes non-unique | Diagnostic scoring less strict on magnitude than v0.1.0 wording |
| A7 | Ratio value columns declare `"ratio": true`; percent-vs-fraction tolerance applies only to them | BENCHMARK.md section 4.2 (clarification) | Reviewer: unconditional fallback matched 100 to 1 | Stricter scoring |
| A8 | S04 reported as correct-abstention (insufficient tasks) and false-abstention (answerable tasks) | report | Matches BENCHMARK.md section 5 | Conformance fix |
