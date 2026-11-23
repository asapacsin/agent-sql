"""Core data contracts (docs/spec/ARCHITECTURE.md section 6). TaskInput carries no gold data."""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["ANSWERED", "INSUFFICIENT_DATA", "FAILED_EXECUTION", "FAILED_REPAIR_BUDGET", "FAILED_TOOL_BUDGET",
                 "FAILED_TOKEN_BUDGET", "FAILED_TIMEOUT", "FAILED_PROVIDER", "FAILED_INVALID_OUTPUT",
                 "FAILED_MODEL_MISMATCH"]
ValidationStatus = Literal["PASSED", "WARNINGS", "FAILED", "NOT_RUN"]


class TaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    task_id: str
    db_id: str
    question: str
    evidence: str | None = None
    as_of_date: date | None = None


class Entity(BaseModel):
    type: Literal["region", "category", "department", "product", "customer", "segment", "event", "period", "channel",
                  "other"]
    value: str


class EvidenceRef(BaseModel):
    query_id: str
    row_index: int
    column: str


class Claim(BaseModel):
    claim_id: str
    text: str
    value: float | str | None = None
    unit: str | None = None
    entities: list[Entity] = Field(default_factory=list)
    evidence: EvidenceRef | None = None

    @property
    def is_numeric(self) -> bool:
        return isinstance(self.value, (int, float)) and not isinstance(self.value, bool)


class ChartHint(BaseModel):
    query_id: str
    kind: Literal["bar", "line", "table"]
    x: str | None = None
    y: str | None = None
    series: str | None = None


class MetricInterpretation(BaseModel):
    metric_id: str
    basis: str
    period: str | None = None


class Uncertainty(BaseModel):
    level: Literal["low", "medium", "high"] = "medium"
    reasons: list[str] = Field(default_factory=list)


class ValidationCheck(BaseModel):
    id: str
    result: Literal["PASS", "WARN", "FAIL", "N/A"]
    detail: str = ""


class ValidationReport(BaseModel):
    status: ValidationStatus = "NOT_RUN"
    checks: list[ValidationCheck] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    status: Status
    answer_text: str = Field(default="", max_length=1200)
    final_query_id: str | None = None
    claims: list[Claim] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    charts: list[ChartHint] = Field(default_factory=list)
    metric_interpretations: list[MetricInterpretation] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    uncertainty: Uncertainty = Field(default_factory=Uncertainty)
    validation: ValidationReport = Field(default_factory=ValidationReport)

    @classmethod
    def failure(cls, status: Status, reason: str) -> FinalAnswer:
        """A failure answer never carries numeric claims (FR-06)."""
        return cls(status=status, answer_text=f"No answer: {reason}"[:1200], limitations=[reason],
                   uncertainty=Uncertainty(level="high", reasons=[reason]))


class UsageTotals(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cost_usd: float = 0.0
    uncached_cost_usd: float = 0.0


class Counters(BaseModel):
    llm_calls: int = 0
    tool_calls: int = 0
    failures: int = 0
    repairs: int = 0
    verification_queries: int = 0


class RunResult(BaseModel):
    task_id: str
    system_id: str
    run_id: str
    answer: FinalAnswer
    queries: list[dict] = Field(default_factory=list)  # QueryRecord.summary() for each query
    usage: UsageTotals = Field(default_factory=UsageTotals)
    counters: Counters = Field(default_factory=Counters)
    latency_s: float = 0.0
    llm_latency_s: float = 0.0
    db_latency_s: float = 0.0
    served_models: list[str] = Field(default_factory=list)
    retrieved_tables: list[str] | None = None  # initial retrieved schema context (S03); None = full schema
    trace_path: str | None = None
