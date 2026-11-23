# agent-sql — Agentic SQL Analyst (benchmark-driven)

> **Status: SPECIFICATION ONLY (Phase 0, spec v0.1.0, 2026-11-23).** No system has been implemented, no benchmark has run, and **no results exist yet**. Everything below is a design and a plan for measurement.

An autonomous SQL data analyst. It takes a natural-language business question, investigates the database schema, writes and executes read-only SQL, diagnoses failures, validates results, and returns an evidence-backed answer. The project's purpose is to **measure**, with reproducible experiments, which agentic capabilities actually improve SQL accuracy and business-analysis outcomes, and at what latency and cost.

## Central hypothesis

> An agent that can inspect, execute, observe, correct, and validate SQL completes analytical tasks more reliably than a single-pass LLM-to-SQL system.

This is a hypothesis to test, not an assumed result. A null or negative finding will be reported the same way as a positive one.

## Four comparable systems

All four use the same pinned LLM (`claude-opus-5-5`), the same inference settings, databases, permissions, questions, splits, and scoring. The differences are listed explicitly in [BASELINES.md](docs/spec/BASELINES.md).

| | System | What it adds |
| --- | --- | --- |
| **B0** | One-shot SQL | Question + full schema → one SQL → one execution |
| **B1** | Schema retrieval | B0 with BM25 + value-index schema retrieval |
| **A1** | Executable SQL agent | Schema tools, execution feedback, ≤ 3 bounded repairs, grounded answer |
| **A2** | Planning + verification agent (primary) | A1 + task decomposition, intermediate inspection, metric/consistency checks (V1–V8), uncertainty reporting |

The LLM chooses analytical actions. **Deterministic code enforces** SQL safety (single read-only SELECT, no catalogs, no restricted columns), permissions, timeouts, row limits, token/tool/repair budgets, termination, and the rule that an answer may contain only numbers backed by a successful query.

## Planned evaluation

- **Datasets:** BIRD dev (database-disjoint splits; 150 test questions) + **RetailCo**, a synthetic retail BI dataset with precise KPI definitions, data-quality traps, and planted business events (50 test tasks, separate test instance). 200 held-out test tasks in total.
- **Metrics:** execution success (M01) reported **separately from** execution accuracy (M02); end-to-end task success (M03, primary); error recovery; table-selection accuracy; tool calls; latency; tokens; cost per task and per successful task; validation-failure rate; run-to-run reliability; plus grounding, factual consistency, and the silent-wrong-answer rate.
- **Statistics:** 3 runs per configuration, Wilson and bootstrap CIs, exact McNemar paired tests with Holm correction on pre-registered hypotheses H1–H3.
- **Ablations:** schema retrieval, exploration tools, execution feedback, repair budget, planning, verification, and a **compute-matched** self-consistency control that separates agency from simply spending more tokens.

## Intended results (what the final report will contain, not what it will say)

1. For each hypothesis: effect size, 95% CI, adjusted p-value, in whichever direction the data shows.
2. For each capability: **effective**, **harmful**, **cost-only**, or **inconclusive**, with marginal cost per extra success.
3. Cost/latency vs accuracy for B0, B1, A1, A2.
4. A failure taxonomy showing which errors agents fix, introduce, or leave.
5. A list of claims that were **not verified** (e.g. analyst time savings, until the human study runs).

## Specification

| Document | Contents |
| --- | --- |
| [PRD](docs/spec/PRD.md) | Problem, users, scenarios, requirements, UX, business-impact measurement |
| [ARCHITECTURE](docs/spec/ARCHITECTURE.md) | Components, state machines, LLM abstraction, data contracts, tracing, UI, eval pipeline |
| [BASELINES](docs/spec/BASELINES.md) | B0/B1/A1/A2 definitions, fairness contract, budgets, verification checks, ablation flags |
| [TOOLS](docs/spec/TOOLS.md) | Tool I/O schemas, permissions, errors, SQL safety policy, adversarial corpus |
| [BENCHMARK](docs/spec/BENCHMARK.md) | Datasets, splits, leakage controls, scoring, metrics M01–M12 / S01–S07, statistics, failure codes |
| [EXPERIMENTS](docs/spec/EXPERIMENTS.md) | Hypotheses, experiment matrix, canonical parameters, ablations, run protocol, reproducibility, cost planning |
| [DATA_MODEL](docs/spec/DATA_MODEL.md) | RetailCo schema, traps, metric definitions, planted events |
| [IMPLEMENTATION_PLAN](docs/spec/IMPLEMENTATION_PLAN.md) | Phases 1–6: tasks, dependencies, tests, acceptance, definition of done |
| [RISKS](docs/spec/RISKS.md) | Validity, engineering, and claim risks with mitigations |

## How work is run here

This repository adopts the AI-native engineering practice from [asapacsin/ai-native-engineering](https://github.com/asapacsin/ai-native-engineering). See [AGENTS.md](AGENTS.md):

- Each phase has a **JSON task contract** (`docs/spec/contracts/phase-N.json`) with exact gate commands, scope, and budgets.
- **Independent requirement manifests** (`docs/spec/requirements/`) map requirements to gates and evidence levels.
- A **dependency-aware work frontier** (`docs/spec/work.json`) separates autonomous work from human-only work (credentials, budget approval, user study).
- Verdicts are `PASS | REWORK | BLOCKED | NOT_VERIFIED`, bounded to 3 attempts, with evidence bound to a commit.

## Verify the specification

```bash
python3 scripts/check_spec.py
```

```bash
python3 -m unittest discover -s tests/spec -v
```

`check_spec.py` checks cross-document consistency (tools, metrics, failure codes, risk ids, system ids), placeholder and over-claim language, relative links, that the canonical parameters match [EXPERIMENTS.md](docs/spec/EXPERIMENTS.md) §4, contract/requirement/frontier validity, and that the plan is executable in phase order.

## Next step

Phase 1 (scaffold, read-only DB layer, SQL safety validator, RetailCo-dev generator, B0, evaluation harness) starts after the owner approves this specification (`h-approve-spec` in `docs/spec/work.json`). Live-model gates also need an API credential (`h-provide-api-key`).
