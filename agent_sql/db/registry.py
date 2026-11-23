"""Open benchmark databases by db_id ("retailco_dev", "retailco_test", "bird:<db>")."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

from agent_sql.config.settings import REPO_ROOT, sha256_file
from agent_sql.db.executor import Database

BI_DESC = REPO_ROOT / "bi_dataset" / "descriptions.yaml"


@lru_cache(maxsize=1)
def bi_descriptions() -> dict:
    d = yaml.safe_load(BI_DESC.read_text())
    return {"tables": d["tables"], "columns": d["columns"],
            "restricted": {t: set(c) for t, c in d.get("restricted", {}).items()}}


def bi_sqlite_path(instance: str) -> Path:
    return REPO_ROOT / "data" / "generated" / f"retailco_{instance}" / f"retailco_{instance}.sqlite"


def open_bi(instance: str = "dev", engine: str = "sqlite", sqlite_path: Path | None = None) -> Database:
    desc = bi_descriptions()
    db_id = f"retailco_{instance}"
    if engine == "sqlite":
        path = sqlite_path or bi_sqlite_path(instance)
        return Database.from_sqlite(db_id, path, restricted=desc["restricted"], descriptions=desc,
                                    snapshot_hash=sha256_file(path))
    if engine == "postgres":
        from bi_dataset.load_postgres import agent_url

        url = os.environ.get(f"AGENT_SQL_PG_URL_{instance.upper()}") or agent_url(instance)
        return Database.from_postgres(db_id, url, "bi", restricted=desc["restricted"], descriptions=desc)
    raise ValueError(engine)


def open_database(db_id: str, engine: str | None = None) -> Database:
    if db_id.startswith("retailco_"):
        return open_bi(db_id.removeprefix("retailco_"), engine or os.environ.get("AGENT_SQL_BI_ENGINE", "sqlite"))
    if db_id.startswith("bird:"):
        from agent_sql.eval.datasets.bird import open_bird_db  # noqa: PLC0415 - optional dataset

        return open_bird_db(db_id.removeprefix("bird:"))
    raise KeyError(db_id)
