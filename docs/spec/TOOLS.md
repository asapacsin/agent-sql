# Tool Contracts and Safety Boundaries

> **Status:** SPECIFIED (Phase 0). Spec version 0.1.0, 2026-11-23. Schemas below are normative for Phases 3–4. None is implemented.

## 1. General rules

- Tools are registered in `agent_sql/tools/registry.py` with a JSON Schema (`additionalProperties: false`, all fields `required` with nullable types where optional) and are sent to the provider with `strict: true`.
- Every tool call carries a `purpose` string (1–200 chars), shown in the UI action trace. It is the model's declared intent, not its private reasoning.
- The controller validates arguments against the schema **before** dispatch. Invalid arguments → observation `{status: "INVALID_ARGS", detail}`. This does not count as a SQL candidate failure, but it does count toward the tool budget.
- A tool that is not allowed in the current state or system returns `{status: "TOOL_NOT_ALLOWED"}` and counts toward the tool budget.
- Observations are JSON, size-capped at 50 rows and 200 chars per cell, and wrapped as data. Values from the database are never interpreted as instructions.
- **Budget accounting:** data tools (§2) count toward the tool-call budget. Control tools (§3) do not, but each LLM response counts toward the LLM-call budget.
- All tools are **read-only**. No tool can write, create, or drop anything, or reach any network resource except the configured database.
- Permissions are enforced by policy (allowed schemas/tables, restricted columns) and by database privileges. A table outside the allowlist "does not exist" to every tool.

Common error envelope:

```json
{"status": "ERROR", "error_class": "UNKNOWN_TABLE", "message": "...", "hint": "did you mean: orders, order_items"}
```

## 2. Data tools

### `list_tables`

| | |
| --- | --- |
| Purpose | Enumerate accessible tables (and views) with short descriptions |
| Available in | A1, A2 (not when `exploration_tools=off`) |
| Input | `{"purpose": str, "name_filter": str \| null}` (case-insensitive substring) |
| Output | `{"status":"OK","tables":[{"name":str,"description":str\|null,"row_count_estimate":int\|null}]}` |
| Errors | none beyond envelope |
| Permissions | Allowlisted tables only; system catalogs never listed |
| Use when | The retrieved context seems to be missing a needed entity, or the question mentions a concept not in context |

### `describe_table`

| | |
| --- | --- |
| Purpose | Columns, types, nullability, PK, FKs, column descriptions |
| Available in | A1, A2 (not when `exploration_tools=off`) |
| Input | `{"purpose": str, "table": str}` |
| Output | `{"status":"OK","table":str,"description":str\|null,"columns":[{"name","type","nullable","is_pk","description","restricted":bool}],"foreign_keys":[{"columns":[...],"ref_table":str,"ref_columns":[...]}]}` |
| Errors | `UNKNOWN_TABLE` (with edit-distance hint) |
| Permissions | Restricted columns are listed with `restricted: true` so the model knows not to query them; their values are never returned by any tool |
| Use when | Before writing SQL against a table whose columns are not in context, or after an `UNKNOWN_COLUMN` error |

### `inspect_relationships`

| | |
| --- | --- |
| Purpose | Join paths between tables |
| Available in | A1, A2 (not when `exploration_tools=off`) |
| Input | `{"purpose": str, "tables": [str] (1–6)}` |
| Output | `{"status":"OK","edges":[{"from_table","from_columns","to_table","to_columns","cardinality":"many-to-one\|one-to-one\|unknown","source":"declared_fk\|documented"}],"paths":[{"tables":[...],"via":[edge_index...]}]}` (shortest paths, ≤ 3 hops, between every pair of input tables) |
| Errors | `UNKNOWN_TABLE` |
| Permissions | Catalog metadata only |
| Use when | Two or more tables must be joined and the join keys are not obvious |

Cardinality comes from declared PK/FK uniqueness. Relationships documented but not declared (common in BIRD) come from the description files and are marked `documented`.

### `sample_rows`

| | |
| --- | --- |
| Purpose | See representative values (formats, codes, casing) |
| Available in | A1, A2 (not when `exploration_tools=off`) |
| Input | `{"purpose": str, "table": str, "columns": [str] \| null, "n": int (1–5, default 3)}` |
| Output | `{"status":"OK","columns":[...],"rows":[[...]]}` |
| Determinism | `ORDER BY` primary key (or `rowid` for SQLite) `LIMIT n`: same rows every time |
| Errors | `UNKNOWN_TABLE`, `UNKNOWN_COLUMN`, `RESTRICTED_COLUMN` |
| Permissions | Restricted columns are rejected |
| Use when | Filter values' exact spelling or encoding is uncertain (e.g. `'completed'` vs `'Completed'`, date formats) |

### `validate_sql`

| | |
| --- | --- |
| Purpose | Dry-run: safety policy + schema reference check + planner check, with no execution |
| Available in | A1, A2 (also used internally by every system) |
| Input | `{"purpose": str, "sql": str (≤ 20,000 chars)}` |
| Output | `{"status":"OK\|REJECTED","rule_violations":[{"rule":"SR1..SR12","detail"}],"warnings":[{"code","detail"}],"referenced_tables":[...],"referenced_columns":[...]}` |
| Planner check | PostgreSQL `EXPLAIN` (no `ANALYZE`); SQLite `EXPLAIN QUERY PLAN`. Run only after SR1–SR12 pass, inside a read-only transaction |
| Errors | A `REJECTED` result is a SQL candidate failure ([ARCHITECTURE.md](ARCHITECTURE.md) §4.1) |
| Use when | Optional before execution. The model may skip it, because `execute_readonly_sql` always re-validates |

### `execute_readonly_sql`

| | |
| --- | --- |
| Purpose | Execute one validated SELECT and store its result |
| Available in | A1, A2 (B0/B1 call the same executor internally, once) |
| Input | `{"purpose": str, "sql": str, "label": str \| null}` |
| Output (OK) | `{"status":"OK","query_id":"q7","columns":[{"name","type"}],"row_count":int,"truncated":bool,"rows_preview":[[...]] (≤ 50),"elapsed_ms":int}` |
| Output (failure) | `{"status":"ERROR\|TIMEOUT\|REJECTED","error_class":..., "message":..., "hint":..., "query_id":"q8"}` |
| Enforcement | 1) full `validate_sql` (SR1–SR12); 2) `READ ONLY` transaction, always rolled back; 3) statement timeout 30 s; 4) `fetchmany` up to 10,000 stored rows (`truncated=true` beyond); 5) result stored under `query_id` |
| Errors | All failure statuses are SQL candidate failures |
| `execution_feedback=binary` | Observation reduced to `{"status": ..., "query_id": ...}` |
| Use when | A candidate query is ready |

### `get_query_result_metadata`

| | |
| --- | --- |
| Purpose | Profile a stored result without re-executing |
| Available in | A1, A2 |
| Input | `{"purpose": str, "query_id": str, "columns": [str] \| null}` |
| Output | `{"status":"OK","row_count","truncated","columns":[{"name","type","null_count","distinct_count","min","max","sum"\|null,"top_values":[[value,count]] (≤ 5)}],"duplicate_rows":int}` |
| Errors | `UNKNOWN_QUERY_ID`, `UNKNOWN_COLUMN` |
| Use when | Checking plausibility: nulls, duplicates, ranges, totals, or whether a preview is representative |

### `compare_query_results`

| | |
| --- | --- |
| Purpose | Deterministic comparison of two stored results (reconciliation, consistency) |
| Available in | A2 only |
| Input | `{"purpose": str, "left_query_id": str, "right_query_id": str, "mode": "rows\|aggregate", "key_columns": [str] \| null, "value_columns": [[left_col, right_col]], "aggregate": "sum\|count\|none", "rel_tolerance": float (default 0.005)}` |
| Output (`rows`) | `{"status":"OK","matched":int,"mismatched":[{"key","left","right","rel_diff"}] (≤ 20),"left_only":int,"right_only":int}` |
| Output (`aggregate`) | `{"status":"OK","left_value","right_value","rel_diff","within_tolerance":bool}` |
| Errors | `UNKNOWN_QUERY_ID`, `UNKNOWN_COLUMN`, `KEY_NOT_UNIQUE` |
| Use when | Checking that a breakdown sums to a total (V3), or that two queries agree on a shared figure (V8) |

## 3. Control tools (no data access)

### `submit_plan` (A2)

Input: `{"steps":[{"step_id":"s1","goal":str,"expected_output":"scalar|table|list","metric_ids":[str],"depends_on":[str]}] (1–5), "periods":[{"label":str,"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}], "assumptions":[str]}`.
The controller runs PLAN_CHECK and replies `{"status":"ACCEPTED"}` or `{"status":"REJECTED","problems":[...]}`. Allowed once at PLAN, once more on re-plan.

### `complete_step` (A2)

Input: `{"step_id": str, "query_id": str | null, "summary": str (≤ 300), "unanswerable_reason": str | null}`. Exactly one of `query_id` or `unanswerable_reason` must be non-null, and `query_id` must be a successful execution. Triggers INSPECT.

### `submit_answer` (A1, A2)

Input: the `FinalAnswer` fields the model controls: `status` (`ANSWERED` or `INSUFFICIENT_DATA` only; failure statuses are set by the controller), `answer_text`, `final_query_id`, `answer_columns`, `claims`, `tables`, `charts`, `metric_interpretations`, `assumptions`, `limitations`, `uncertainty`. The controller fills `validation` and enforces the answer contract ([ARCHITECTURE.md](ARCHITECTURE.md) §4.2).

**Answer projection (CHANGELOG A23).** With `status = ANSWERED`, `answer_columns` is required: the columns of `final_query_id` that directly answer the question, in answer order (unknown, repeated or ambiguous duplicate column names are rejected once with feedback, like other contract violations; the budget FINALIZE path accepts a missing list). After the loop ends (and after A2 verification), the controller deterministically builds `SELECT <answer_columns> FROM (<final query>) AS answer_src`, runs it through the same validator and read-only executor, and makes that projection the `final_query_id` that is scored. The model only chooses columns; it cannot change the analysis at this step. Claims keep citing any executed query.

## 4. SQL safety policy (deterministic, `agent_sql/safety/validator.py`)

Parsing uses `sqlglot` with the database's dialect (`sqlite` or `postgres`). Any rule failure → `REJECTED`.

| Rule | Requirement |
| --- | --- |
| SR1 | Parses without error in the configured dialect |
| SR2 | Exactly one statement. A trailing `;` is allowed; any second statement is rejected, even when empty-looking or hidden in comments |
| SR3 | Root is `SELECT`, a set operation (`UNION`/`INTERSECT`/`EXCEPT`) of SELECTs, or `WITH … SELECT` |
| SR4 | No DML/DDL/DCL/TCL node anywhere in the tree (including inside CTEs and subqueries): INSERT, UPDATE, DELETE, MERGE, UPSERT, CREATE, DROP, ALTER, TRUNCATE, GRANT, REVOKE, BEGIN, COMMIT, ROLLBACK, SAVEPOINT, SET, RESET, COPY, VACUUM, ANALYZE, REINDEX, ATTACH, DETACH, PRAGMA, LOAD, CALL, DO, LOCK, `SELECT … INTO`, `FOR UPDATE/SHARE`; and any sqlglot `Command` (unparsed passthrough) node |
| SR5 | No denied functions: `pg_sleep*`, `pg_read_file`, `pg_read_binary_file`, `pg_ls_dir`, `pg_stat_file`, `lo_*`, `dblink*`, `set_config`, `current_setting`, `pg_terminate_backend`, `pg_cancel_backend`, `pg_advisory_*`, `txid_*`, `query_to_xml*`, `load_extension`, `readfile`, `writefile`, `fts3_tokenizer`, `randomblob` with argument > 1 MB. An unknown function is allowed only if the dialect parser recognizes it as a built-in aggregate or scalar function on the allowlist in `safety/policy.py` |
| SR6 | Every referenced table (CTE names excluded) resolves to the allowlist for the database. Schema-qualified references outside the allowed schema are rejected |
| SR7 | No system catalogs: `pg_catalog.*`, `information_schema.*`, `pg_*` relations, `sqlite_master`, `sqlite_schema`, `sqlite_temp_master`, `sqlite_sequence`, `dbstat` |
| SR8 | No restricted columns referenced anywhere, `*` included. `SELECT *` over a table with restricted columns is rejected with a hint to list columns |
| SR9 | Every referenced column exists in its resolved table (schema reference check; ambiguous unqualified columns → `AMBIGUOUS_COLUMN`) |
| SR10 | SQL length ≤ 20,000 characters; parse-tree depth ≤ 64; ≤ 16 joins |
| SR11 | No parameters/placeholders (`?`, `$1`, `:name`) and no `\` meta-commands |
| SR12 | No recursive CTE without a terminating `LIMIT` or a bounded-depth predicate; otherwise rejected as potentially unbounded |

Warnings (non-blocking): `CARTESIAN_JOIN` (join without predicate), `NO_LIMIT_LARGE_TABLE` (no aggregate or LIMIT on a table above 1M rows), `IMPLICIT_CAST_DATE` (string/date comparison).

### 4.1 Defense in depth

| Layer | Mechanism | Stops |
| --- | --- | --- |
| L1 | Validator SR1–SR12 | Writes, multi-statement injection, catalog access, restricted data, resource abuse |
| L2 | PostgreSQL role `agent_ro`: SELECT-only grants on `bi`, column grants excluding restricted columns, `default_transaction_read_only=on`, `statement_timeout=30s` | Anything L1 misses |
| L3 | Every execution in a `READ ONLY` transaction, rolled back | Side effects from functions |
| L4 | SQLite: `mode=ro` URI, `PRAGMA query_only=ON`, `set_authorizer` allowing only `SQLITE_SELECT`, `SQLITE_READ`, `SQLITE_FUNCTION` (non-denied), progress-handler timeout | Writes, ATTACH, runaway queries |
| L5 | Process: DB credentials only in environment; the LLM never sees connection strings; traces redact them | Credential exposure |

L2 and L4 are authoritative. L1 gives early, explainable rejections. A test must show that, with L1 disabled, each corpus attack still fails at L2/L4 (integration test `test_defense_without_validator`).

### 4.2 Adversarial corpus (`tests/safety_corpus/`)

At least 40 must-reject cases across: stacked statements (`SELECT 1; DROP TABLE orders`), comment-hidden statements, DML in CTEs (`WITH x AS (DELETE … RETURNING *) SELECT …`), `SELECT … INTO`, `COPY … TO PROGRAM`, `ATTACH DATABASE`, `PRAGMA writable_schema`, `pg_sleep(100)`, `pg_read_file('/etc/passwd')`, `dblink`, `set_config`, catalog reads, restricted-column reads via `*`, aliasing, subqueries, or `row_to_json`, unicode/whitespace obfuscation, case variants, recursive-CTE bombs, and huge `randomblob`. At least 20 must-pass benign cases include CTEs, window functions, `UNION ALL`, `CASE`, date arithmetic, and string functions. Corpus files are YAML: `{sql, dialect, expect: REJECT|PASS, rule}`.

## 5. Tool selection and observation interpretation

The LLM selects tools. The prompt gives guidance and the controller enforces bounds. Guidance encoded in `prompts/a1/system.v1.md` and `prompts/a2/system.v1.md`:

| Situation | Expected next action |
| --- | --- |
| Context lacks a needed concept | `list_tables(name_filter)`, then `describe_table` |
| Join keys unclear | `inspect_relationships` |
| Filter literal uncertain | `sample_rows` on the column, or `get_query_result_metadata` top values |
| `UNKNOWN_COLUMN` / `UNKNOWN_TABLE` | Use the `hint`, then `describe_table`. Do not guess repeatedly |
| `TIMEOUT` | Add aggregation or filters, or reduce joins; check `CARTESIAN_JOIN` warnings |
| `OK` but 0 rows | Check filter literals (`sample_rows`) and the period. Empty may be a correct answer, and the model should say so |
| `OK`, suspicious magnitude | `get_query_result_metadata` (duplicates, nulls); for A2, a fan-out check against the grain |
| Breakdown reported (A2) | `compare_query_results` against a total query (V3) |
| Data for the question does not exist | `submit_answer(status=INSUFFICIENT_DATA)` with the missing data named in `limitations` |

**When is another action justified?** The controller does not judge justification; budgets bound it. Justification is *measured*: tool calls per task (M06), plus ablations showing whether extra actions buy success (AB3, AB5, AB7). The prompts state: stop and answer once the final query is executed, inspected, and (A2) its checks are run, and do not run queries that cannot change the answer.
