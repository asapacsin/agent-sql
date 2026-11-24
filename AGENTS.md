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

## Development cost discipline

These rules cover the tokens a contributor (human with an AI assistant, or a coding agent) spends while **building** the project. The cost of **running** the systems under evaluation is governed separately by [EXPERIMENTS.md section 9](docs/spec/EXPERIMENTS.md#9-cost-model-and-cost-control) (R1–R9).

**Context: load only what the task needs.**

- **D1 Narrow entry.** Start from `docs/spec/work.json` (frontier item) and the active `docs/spec/contracts/phase-N.json`. Read only the spec sections those name; do not read all of `docs/spec/` at session start.
- **D2 Search before read.** Locate code with grep/glob first, then read the relevant line range. Read a whole file only when editing most of it or when it is under ~300 lines.
- **D3 Never load bulk artifacts.** Do not open datasets, databases, traces, `runs/`, replay fixtures or generated reports in full. Inspect with `head`, `wc`, `jq` on selected fields, or a short script that prints a summary. Frozen test artifacts stay unread (Engineering invariants).
- **D4 Do not re-read.** A file already read or just edited in the session is not read again unless it changed outside the session.

**Commands: keep output short.**

- **D5 Quiet gates.** Run tests with `-q` (pytest) or without `-v` (unittest) during iteration, and target the affected test module first; run the full gate set once before finishing. Pipe long logs through `tail -n 50` or `grep` for the failing lines.
- **D6 No live LLM calls during development.** Develop and test against FakeLLM and the record/replay cache (`tests/replay/`). A live model call is an experiment run and follows EXPERIMENTS §9.3 (smoke of 5 tasks, spend cap, one runner per directory).
- **D7 Fail fast.** Stop a command as soon as its output shows the failure; do not wait for a long job to finish to read an error already printed.

**Session shape.**

- **D8 One work item per session.** A session takes one frontier item from `docs/spec/work.json` to a verdict. Start a new session (or clear context) for the next item; carry state through commits, `work.json` and the CHANGELOG, not through conversation history.
- **D9 Sub-agents only for broad work.** Spawn a sub-agent only for a wide search or an independent review (Workflow step 4); each one starts cold and re-reads context. Do small lookups inline.
- **D10 Plan before large edits.** For a change touching more than ~3 files, write the plan (files, seams, gates) first, then edit; do not explore by trial edits.
- **D11 Bounded attempts apply to tokens too.** Workflow step 6 (3 attempts, escalate after 2 failures) is also the token stop rule: a failing approach is not retried with more context, it is written down and escalated.

**Spec and code size.**

- **D12 Short, linked documents.** Spec documents state a rule once and link to it elsewhere; no duplicated tables between documents. Record changes as one CHANGELOG row, not by rewriting history in other sections.
- **D13 Small modules with one owner.** Keep modules focused (Workflow step 2) so a task needs few files in context.

**Reporting.** A session that ends a work item states what it verified and what it did not (Evidence discipline). Token use is not a gate, but a session that hit D11 or needed more than one context window records why in the frontier item's notes.

## Gate entry points

```bash
python3 scripts/check_spec.py
```

```bash
python3 -m unittest discover -s tests/spec -v
```

Later phases add the gates listed in their contracts (`docs/spec/contracts/phase-N.json`). The contracts are compatible with `ai_native.py validate` from the ai-native-engineering repository.
