from agent_sql.db.executor import QueryRecord, QueryResult, ResultStore
from agent_sql.systems.base import enforce_answer_contract
from agent_sql.systems.contracts import Claim, EvidenceRef, FinalAnswer, TaskInput

GOLDISH = {"gold", "sql", "answer", "answer_spec", "expected", "solution", "label"}


def test_task_input_has_no_gold_fields():
    assert not (set(TaskInput.model_fields) & GOLDISH)


def test_failure_answer_has_no_numeric_claims():
    a = FinalAnswer.failure("FAILED_EXECUTION", "boom")
    assert a.claims == [] and a.uncertainty.level == "high"


def _store_with(rows):
    s = ResultStore()
    s.add(QueryRecord("q1", "SELECT 1", None, "OK", result=QueryResult(["value"], ["INT"], rows, False)))
    s.add(QueryRecord("q2", "SELECT x", None, "ERROR", error_class="DB_ERROR"))
    return s


def test_answered_requires_successful_final_query():
    a = FinalAnswer(status="ANSWERED", final_query_id="q2",
                    claims=[Claim(claim_id="c1", text="n", value=5, evidence=EvidenceRef(query_id="q1", row_index=0,
                                                                                       column="value"))])
    enforce_answer_contract(a, _store_with([(5,)]))
    assert a.status == "FAILED_INVALID_OUTPUT" and a.claims == []


def test_invalid_evidence_is_dropped():
    a = FinalAnswer(status="ANSWERED", final_query_id="q1",
                    claims=[Claim(claim_id="c1", text="n", value=5,
                                  evidence=EvidenceRef(query_id="q1", row_index=9, column="value"))])
    enforce_answer_contract(a, _store_with([(5,)]))
    assert a.status == "ANSWERED" and a.claims[0].evidence is None
