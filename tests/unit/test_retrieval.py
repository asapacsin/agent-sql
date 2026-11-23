"""Retrieval is deterministic and built only from the schema (leakage control, BENCHMARK.md 3.4)."""
from __future__ import annotations

import pytest

from agent_sql.retrieval.schema_index import SchemaIndex, tokenize


def test_tokenize():
    assert tokenize("OrderItems order_date of the Customers") == ["order", "item", "order", "date", "customer"]


def test_index_build_reads_no_tasks(monkeypatch, bi_dev_sqlite):
    import agent_sql.eval.datasets.bi as bi
    import agent_sql.eval.datasets.bird as bird

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("retrieval index must not read benchmark tasks")

    import builtins
    import pathlib

    real_open, real_read = builtins.open, pathlib.Path.read_text
    forbidden = ("bi_dataset/tasks", "dev.json", "data/splits")

    def guarded_open(file, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        if any(f in str(file) for f in forbidden):
            raise AssertionError(f"retrieval index read {file}")
        return real_open(file, *a, **k)

    def guarded_read(self, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        if any(f in str(self) for f in forbidden):
            raise AssertionError(f"retrieval index read {self}")
        return real_read(self, *a, **k)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(pathlib.Path, "read_text", guarded_read)
    monkeypatch.setattr(bi, "load_bi_tasks", boom)
    monkeypatch.setattr(bird, "dev_records", boom)
    monkeypatch.setattr(bird, "load_bird_tasks", boom)
    idx = SchemaIndex.build(bi_dev_sqlite)
    assert idx.tables


def test_retrieval_deterministic_and_fk_expanded(bi_dev_sqlite):
    a = SchemaIndex.build(bi_dev_sqlite).retrieve("net revenue by product category last quarter")
    b = SchemaIndex.build(bi_dev_sqlite).retrieve("net revenue by product category last quarter")
    assert a == b and "categories" in a and "order_items" in a


def test_restricted_values_never_indexed(bi_dev_sqlite):
    idx = SchemaIndex.build(bi_dev_sqlite)
    assert not any(col in ("email", "phone") for cols in idx.values.values() for _, col in cols)
    assert not any("@example.com" in v for v in idx.values)


def test_render_marks_restricted_columns(bi_dev_sqlite):
    text = SchemaIndex.build(bi_dev_sqlite).render(["customers"], "Which customers?")
    assert "RESTRICTED" in text


@pytest.fixture(scope="module")
def bi_dev_sqlite():
    from agent_sql.db.registry import bi_sqlite_path, open_bi

    if not bi_sqlite_path("dev").exists():
        pytest.skip("RetailCo-dev not generated")
    return open_bi("dev", "sqlite")
