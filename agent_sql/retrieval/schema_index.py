"""Deterministic schema retrieval for B1/A1/A2 (docs/spec/BASELINES.md section 3, B1).

Index inputs are ONLY the catalog (names, types, FKs, descriptions) and distinct values read from the database.
Questions and gold SQL are never indexed (leakage test: tests/unit/test_retrieval.py).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rank_bm25 import BM25Okapi

from agent_sql.db.executor import Database, Executor, ResultStore
from agent_sql.systems.base import render_table

TOKEN = re.compile(r"[a-z0-9]+")
STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "by", "with", "what", "which", "how", "many",
        "is", "are", "was", "were", "did", "do", "does", "per", "each", "from", "that", "this", "their", "our", "we",
        "show", "list", "give", "me", "all", "most", "top", "who", "whose", "has", "have", "had", "be", "as", "at"}
MAX_DISTINCT = 10_000
MAX_VALUE_LEN = 60


def tokenize(text: str) -> list[str]:
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "").replace("_", " ").lower()
    toks = [t for t in TOKEN.findall(text) if t not in STOP]
    # light stemming: plural -> singular
    return [t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t for t in toks]


@dataclass
class SchemaIndex:
    db: Database
    tables: list[str]
    table_bm25: BM25Okapi
    column_docs: list[tuple[str, str]]  # (table, column)
    column_bm25: BM25Okapi
    values: dict[str, set[tuple[str, str]]] = field(default_factory=dict)  # normalized value -> {(table, col)}
    samples: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    @classmethod
    def build(cls, db: Database, *, with_values: bool = True, timeout_s: float = 30.0) -> SchemaIndex:
        tables, tdocs, cdocs, ckeys = [], [], [], []
        cat = db.doc_catalog
        for name in cat.names:
            t = cat.tables[name]
            tables.append(name)
            words = [name, t.description or ""] + [f"{c.name} {c.description or ''}" for c in t.columns]
            tdocs.append(tokenize(" ".join(words)) or ["_"])
            for c in t.columns:
                if c.restricted:
                    continue
                ckeys.append((name, c.name))
                cdocs.append(tokenize(f"{name} {c.name} {c.description or ''}") or ["_"])
        idx = cls(db, tables, BM25Okapi(tdocs), ckeys, BM25Okapi(cdocs))
        if with_values:
            idx._build_values(timeout_s)
        return idx

    def _build_values(self, timeout_s: float) -> None:
        ex = Executor(self.db, statement_timeout_s=timeout_s, max_rows=MAX_DISTINCT + 1)
        q = '"' if self.db.dialect == "sqlite" else '"'
        for name in self.tables:
            t = self.db.catalog.tables.get(name)
            if t is None:
                continue
            for c in t.columns:
                if c.restricted or not any(k in c.type.upper() for k in ("CHAR", "TEXT", "CLOB", "STRING")):
                    continue
                sql = (f"SELECT DISTINCT {q}{c.name}{q} FROM {q}{name}{q} WHERE {q}{c.name}{q} IS NOT NULL "
                       f"LIMIT {MAX_DISTINCT + 1}")
                rec = ex.run(sql, ResultStore())
                if not rec.ok or rec.result is None or rec.result.truncated:
                    continue
                vals = sorted(str(r[0]) for r in rec.result.rows if r[0] is not None)
                self.samples[(name, c.name)] = [v[:MAX_VALUE_LEN] for v in vals[:3]]
                for v in vals:
                    if 0 < len(v) <= MAX_VALUE_LEN:
                        self.values.setdefault(v.strip().lower(), set()).add((name, c.name))

    # ------------------------------------------------------------------ query
    def value_hits(self, question: str) -> dict[str, list[tuple[str, str, str]]]:
        words = re.findall(r"[A-Za-z0-9][A-Za-z0-9&'\-\.]*", question)
        hits: dict[str, list[tuple[str, str, str]]] = {}
        for n in (3, 2, 1):
            for i in range(len(words) - n + 1):
                gram = " ".join(words[i:i + n]).strip(".").lower()
                if len(gram) < 3 or (n == 1 and gram in STOP):
                    continue
                for table, col in sorted(self.values.get(gram, ())):
                    hits.setdefault(table, []).append((col, gram, n))
        return hits

    def retrieve(self, question: str, evidence: str | None = None, top_k: int = 5) -> list[str]:
        query = tokenize(f"{question} {evidence or ''}")
        t_scores = self.table_bm25.get_scores(query) if query else [0.0] * len(self.tables)
        c_scores = self.column_bm25.get_scores(query) if query else [0.0] * len(self.column_docs)
        col_best: dict[str, float] = {}
        for (table, _), s in zip(self.column_docs, c_scores, strict=True):
            col_best[table] = max(col_best.get(table, 0.0), float(s))
        t_rank = {t: r for r, (t, _) in enumerate(sorted(zip(self.tables, t_scores, strict=True),
                                                          key=lambda x: (-x[1], x[0])))}
        c_rank = {t: r for r, t in enumerate(sorted(self.tables, key=lambda t: (-col_best.get(t, 0.0), t)))}
        vhits = self.value_hits(f"{question} {evidence or ''}")
        fused = {}
        for t in self.tables:
            score = 1 / (60 + t_rank[t]) + 1 / (60 + c_rank[t])
            if t_scores[self.tables.index(t)] <= 0 and col_best.get(t, 0.0) <= 0:
                score *= 0.5
            score += 0.02 * len(vhits.get(t, []))
            fused[t] = score
        chosen = [t for t, _ in sorted(fused.items(), key=lambda x: (-x[1], x[0]))[:top_k]]
        # 1-hop FK expansion (both directions), deduplicated, deterministic order
        expanded = list(chosen)
        cat = self.db.doc_catalog
        for t in chosen:
            for fk in cat.tables[t].foreign_keys:
                if fk.ref_table in cat.tables and fk.ref_table not in expanded:
                    expanded.append(fk.ref_table)
            for other in self.tables:
                if other not in expanded and any(fk.ref_table == t for fk in cat.tables[other].foreign_keys):
                    expanded.append(other)
        return expanded

    def render(self, tables: list[str], question: str | None = None) -> str:
        vh = self.value_hits(question) if question else {}
        out = [f"Database: {self.db.db_id} (dialect: {'PostgreSQL' if self.db.dialect == 'postgres' else 'SQLite'})"]
        for name in tables:
            out.append(render_table(self.db.doc_catalog.tables[name]))
            for (tname, col), vals in sorted(self.samples.items()):
                if tname == name and vals:
                    out.append(f"  sample values {col}: {', '.join(repr(v) for v in vals)}")
            for col, gram, _ in vh.get(name, [])[:5]:
                out.append(f"  question value {gram!r} found in {name}.{col}")
        return "\n".join(out)
