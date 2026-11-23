# Agent operating contract — agent-sql

Adapted from [asapacsin/ai-native-engineering](https://github.com/asapacsin/ai-native-engineering) (`AGENTS.md`, `docs/AI_BUILDER_BIBLE.md`). Applies to every human or AI contributor.

## Workflow

1. **Specification first.** The specification in `docs/spec/` is the source of truth. Implementation follows the phase contracts; a change in behavior starts with a spec change in the same PR.
2. **Inspect before acting.** Read the relevant spec section, the phase contract, `docs/spec/work.json`, `git status`, and the existing owner module before editing. Improve existing seams; never create a parallel implementation of a component that already has an owner (validator, executor, budgets, metric registry, trace writer).
3. **Work from a contract.** Each unit of work maps to a frontier item in `docs/spec/work.json` and its phase contract `docs/spec/contracts/phase-N.json` (goal, acceptance, boundaries, approved and forbidden paths, exact gates, budgets).
4. **Run the gates, then get an independent review.** Run the contract's gate commands exactly. Record commit SHA, commands, and observed output. A reviewer other than the executor inspects the diff and the evidence. A reviewer's words never override a failed required gate.
5. **Verdicts:** `PASS` (all required gates pass on the reviewed commit, plus independent review), `REWORK` (targeted fixes), `BLOCKED` (named missing dependency, credential, or resource), `NOT_VERIFIED` (the evidence cannot support a completion claim).
6. **Bounded attempts.** At most 3 attempts per task. After two failed attempts, or on an architecture conflict, escalate: write the problem down and ask the owner. Never loop indefinitely.
7. **Continue independent work.** A blocked task (no API key, no network to BIRD, no Postgres) blocks only its dependents. Batch human requests in the frontier instead of interrupting for each one.

## Engineering invariants (project-specific)

- The LLM chooses analytical actions; **deterministic code enforces** SQL safety, permissions, limits, budgets, termination, and answer contracts. Never move an enforcement rule into a prompt.
- No write path to any database. Every SQL statement passes `agent_sql/safety/validator.py` and runs on a read-only connection.
- No silent retries. Every retry is counted against a budget and logged.
- Never fabricate database answers. When execution fails, the answer carries a `FAILED_*` or `INSUFFICIENT_DATA` status and no numeric claims.
- Prompts and model configuration are versioned files. Edit by adding a new version, never in place.
- Systems never see gold data. `agent_sql.systems` must not import `agent_sql.eval`.
- Do not read or edit frozen test artifacts (`data/splits/bird.json`, `bi_dataset/tasks/test.jsonl`, `bi_dataset/events/test.json`) during Phases 3–5.
- No new infrastructure (agent frameworks, vector DBs, queues) without an ADR showing a measured need.

## Evidence discipline

- Results bind to a commit and a run manifest. Compile ≠ runtime; FakeLLM ≠ live model; replay ≠ fresh sample; dev/val ≠ test.
- Never claim a performance improvement that a frozen, reported experiment has not measured. Report null and negative results.
- State explicitly what was **not** verified.
- No secrets in code, configs, traces, reports, commits, or prompts. Credentials come from the environment only.

## Gate entry points

```bash
python3 scripts/check_spec.py
```

```bash
python3 -m unittest discover -s tests/spec -v
```

Later phases add the gates listed in their contracts (`docs/spec/contracts/phase-N.json`). The contracts are compatible with `ai_native.py validate` from the ai-native-engineering repository.
