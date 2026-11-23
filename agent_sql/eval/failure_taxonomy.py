"""Automatic failure codes (docs/spec/BENCHMARK.md section 7). Hand-labeled codes are added in E7."""
from __future__ import annotations

STATUS_CODES = {
    "FAILED_REPAIR_BUDGET": "F-BUDGET", "FAILED_TOOL_BUDGET": "F-BUDGET", "FAILED_TOKEN_BUDGET": "F-BUDGET",
    "FAILED_TIMEOUT": "F-BUDGET", "FAILED_PROVIDER": "F-PROVIDER", "FAILED_MODEL_MISMATCH": "F-PROVIDER",
    "FAILED_INVALID_OUTPUT": "F-ANSWER-UNGROUNDED",
}
ERROR_CODES = {
    "PARSE_ERROR": "F-PARSE", "POLICY_VIOLATION": "F-SAFETY", "RESTRICTED_COLUMN": "F-SAFETY",
    "UNKNOWN_TABLE": "F-SCHEMA-TABLE", "UNKNOWN_COLUMN": "F-SCHEMA-COLUMN", "AMBIGUOUS_COLUMN": "F-SCHEMA-COLUMN",
    "TIMEOUT": "F-EXEC-TIMEOUT",
}


def auto_failure_code(*, success: bool, status: str, task_type: str, last_error_class: str | None,
                      pred_empty: bool, gold_empty: bool, tables_exact: bool | None, abstained: bool,
                      numeric_claims_on_insufficient: bool, ungrounded_claims: bool) -> str | None:
    if success:
        return None
    if status in STATUS_CODES:
        return STATUS_CODES[status]
    if status == "FAILED_EXECUTION":
        return ERROR_CODES.get(last_error_class or "", "F-EXEC-ERROR")
    if task_type == "insufficient":
        return "F-FABRICATION" if numeric_claims_on_insufficient or not abstained else "F-ANSWER-INTERP"
    if abstained:
        return "F-ABSTAIN-WRONG"
    if pred_empty and not gold_empty:
        return "F-EMPTY"
    if tables_exact is False:
        return "F-SCHEMA-TABLE"
    if ungrounded_claims:
        return "F-ANSWER-UNGROUNDED"
    return "UNLABELED"  # requires hand labeling in E7 (F-JOIN, F-FILTER, F-AGG, F-METRIC, F-PERIOD, ...)
