"""Schema catalog via the SQLAlchemy inspector, plus human descriptions."""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import inspect
from sqlalchemy.engine import Engine


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    is_pk: bool
    description: str | None = None
    restricted: bool = False


@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    source: str = "declared_fk"


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    description: str | None = None
    row_count_estimate: int | None = None

    @property
    def pk(self) -> list[str]:
        return [c.name for c in self.columns if c.is_pk]

    def column(self, name: str) -> ColumnInfo | None:
        return next((c for c in self.columns if c.name.lower() == name.lower()), None)


@dataclass
class Catalog:
    tables: dict[str, TableInfo]  # lower-case name -> info

    def table(self, name: str) -> TableInfo | None:
        return self.tables.get(name.lower())

    @property
    def names(self) -> list[str]:
        return sorted(self.tables)


def load_catalog(engine: Engine, schema: str | None = None, *,
                 table_descriptions: dict[str, str] | None = None,
                 column_descriptions: dict[str, dict[str, str]] | None = None,
                 restricted: dict[str, set[str]] | None = None,
                 documented_fks: list[tuple[str, list[str], str, list[str]]] | None = None,
                 allowlist: set[str] | None = None) -> Catalog:
    insp = inspect(engine)
    tdesc = {k.lower(): v for k, v in (table_descriptions or {}).items()}
    cdesc = {k.lower(): {c.lower(): d for c, d in v.items()} for k, v in (column_descriptions or {}).items()}
    restr = {k.lower(): {c.lower() for c in v} for k, v in (restricted or {}).items()}
    tables: dict[str, TableInfo] = {}
    for name in insp.get_table_names(schema=schema):
        lname = name.lower()
        if lname.startswith("sqlite_") or (allowlist is not None and lname not in allowlist):
            continue
        pk = set(insp.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
        cols = [ColumnInfo(c["name"], str(c["type"]), bool(c.get("nullable", True)), c["name"] in pk,
                           cdesc.get(lname, {}).get(c["name"].lower()), c["name"].lower() in restr.get(lname, set()))
                for c in insp.get_columns(name, schema=schema)]
        fks = [ForeignKey(list(fk["constrained_columns"]), fk["referred_table"].lower(), list(fk["referred_columns"]))
               for fk in insp.get_foreign_keys(name, schema=schema) if fk.get("referred_table")]
        tables[lname] = TableInfo(lname, cols, fks, tdesc.get(lname))
    for src, src_cols, dst, dst_cols in documented_fks or []:
        t = tables.get(src.lower())
        if t and not any(fk.ref_table == dst.lower() and fk.columns == src_cols for fk in t.foreign_keys):
            t.foreign_keys.append(ForeignKey(src_cols, dst.lower(), dst_cols, "documented"))
    return Catalog(tables)
