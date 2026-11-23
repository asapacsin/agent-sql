from __future__ import annotations

from pathlib import Path

import yaml

from agent_sql.safety.validator import validate_sql

CORPUS = yaml.safe_load((Path(__file__).parent / "corpus.yaml").read_text())


def _applies(case: dict, dialect: str) -> bool:
    return case.get("dialect", "both") in ("both", dialect)


def test_corpus_size_meets_spec():
    assert len(CORPUS["reject"]) >= 40
    assert len(CORPUS["pass"]) >= 20


def test_must_reject(toy_policy):
    failures = []
    for case in CORPUS["reject"]:
        if not _applies(case, toy_policy.dialect):
            continue
        result = validate_sql(case["sql"], toy_policy)
        if result.ok:
            failures.append(f"ADMITTED [{case['rule']}] {case['sql']!r}")
        elif result.violations[0].rule != case["rule"]:
            failures.append(f"wrong rule {result.violations[0].rule} != {case['rule']} for {case['sql']!r}: "
                            f"{result.message()}")
    assert not failures, "\n".join(failures)


def test_must_pass(toy_policy):
    failures = []
    for sql in CORPUS["pass"]:
        result = validate_sql(sql, toy_policy)
        if not result.ok:
            failures.append(f"REJECTED {sql!r}: {result.message()}")
    assert not failures, "\n".join(failures)
