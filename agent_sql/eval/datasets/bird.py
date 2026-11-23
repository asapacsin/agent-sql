"""BIRD dev loader (docs/spec/BENCHMARK.md sections 1 and 3.1). Gold stays evaluator-side."""
from __future__ import annotations

import csv
import io
import json
from functools import lru_cache
from pathlib import Path

from agent_sql.config.settings import REPO_ROOT, sha256_file
from agent_sql.db.executor import Database
from agent_sql.eval.datasets.bi import EvalTask

BIRD_ROOT = REPO_ROOT / "data" / "downloads" / "bird" / "dev"
DEV_JSON = BIRD_ROOT / "dev.json"
DB_ROOT = BIRD_ROOT / "dev_databases"
SPLIT_FILE = REPO_ROOT / "data" / "splits" / "bird.json"


def require_download() -> None:
    if not DEV_JSON.exists():
        raise FileNotFoundError("BIRD dev not downloaded: python3 scripts/verify_bird_manifest.py --download")


@lru_cache(maxsize=1)
def dev_records() -> dict[str, dict]:
    require_download()
    return {str(r["question_id"]): r for r in json.loads(DEV_JSON.read_text())}


def _read_csv(path: Path) -> list[dict]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    return list(csv.DictReader(io.StringIO(text)))


@lru_cache(maxsize=16)
def descriptions(db: str) -> dict:
    tables, columns = {}, {}
    ddir = DB_ROOT / db / "database_description"
    for f in sorted(ddir.glob("*.csv")):
        table = f.stem.lower()
        cols = {}
        for row in _read_csv(f):
            row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
            name = row.get("original_column_name", "")
            if not name:
                continue
            parts = [row.get("column_name", ""), row.get("column_description", "")]
            if row.get("value_description"):
                parts.append("values: " + row["value_description"])
            desc = " | ".join(p for p in dict.fromkeys(parts) if p and p != name)
            cols[name.lower()] = desc[:400]
        columns[table] = cols
    return {"tables": tables, "columns": columns}


def db_path(db: str) -> Path:
    return DB_ROOT / db / f"{db}.sqlite"


_DBS: dict[str, Database] = {}


def open_bird_db(db: str) -> Database:
    if db not in _DBS:
        _DBS[db] = Database.from_sqlite(f"bird:{db}", db_path(db), descriptions=descriptions(db))
    return _DBS[db]


def load_split() -> dict:
    return json.loads(SPLIT_FILE.read_text())


def split_hash() -> str:
    return sha256_file(SPLIT_FILE)


def load_bird_tasks(split: str) -> list[EvalTask]:
    recs = dev_records()
    out = []
    for qid in load_split()["splits"][split]["question_ids"]:
        r = recs[str(qid)]
        out.append(EvalTask(f"bird-{qid}", "bird", split, f"bird:{r['db_id']}", r["question"], "bird",
                            {"sql": r["SQL"]}, (), r.get("evidence") or None, None, r["difficulty"]))
    return out
