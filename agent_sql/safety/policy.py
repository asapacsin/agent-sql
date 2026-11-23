"""Access policy: allowlisted tables/columns, restricted columns, denied functions, system catalogs."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

SYSTEM_CATALOG_SCHEMAS = {"pg_catalog", "information_schema", "pg_toast"}
SYSTEM_CATALOG_TABLES = {"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema",
                         "sqlite_sequence", "sqlite_stat1", "sqlite_stat4", "dbstat"}
SYSTEM_TABLE_PREFIX = re.compile(r"^(pg_|sqlite_)", re.IGNORECASE)

DENIED_FUNCTION_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"^pg_sleep", r"^pg_read_file$", r"^pg_read_binary_file$", r"^pg_ls_dir$", r"^pg_stat_file$", r"^lo_",
    r"^dblink", r"^set_config$", r"^current_setting$", r"^pg_terminate_backend$", r"^pg_cancel_backend$",
    r"^pg_advisory_", r"^txid_", r"^query_to_xml", r"^load_extension$", r"^readfile$", r"^writefile$",
    r"^fts3_tokenizer$", r"^pg_reload_conf$", r"^pg_rotate_logfile$", r"^pg_logical_", r"^pg_create_",
    r"^pg_drop_", r"^pg_switch_wal$", r"^copy$", r"^edit$", r"^zeroblob$", r"^sqlite_",
)]

# Functions sqlglot may not map to a typed expression but which are known-safe built-ins.
ALLOWED_ANONYMOUS_FUNCTIONS = {
    "strftime", "julianday", "date", "datetime", "time", "unixepoch", "iif", "printf", "format", "instr",
    "substr", "substring", "ifnull", "nullif", "total", "group_concat", "string_agg", "date_part", "date_trunc",
    "to_char", "to_date", "age", "make_date", "extract", "lpad", "rpad", "split_part", "position", "char_length",
    "octet_length", "btrim", "ltrim", "rtrim", "initcap", "regexp_replace", "regexp_matches", "array_agg",
    "generate_series", "percentile_cont", "percentile_disc", "mode", "bool_and", "bool_or", "every", "width_bucket",
    "random", "randomblob", "hex", "unicode", "char", "glob", "like", "likelihood", "typeof", "quote", "sign",
    "trunc", "ceil", "ceiling", "floor", "power", "pow", "sqrt", "exp", "ln", "log", "log10", "mod", "greatest",
    "least", "now", "current_date", "date_add", "date_sub", "datediff", "julian", "cast", "row_number", "rank",
    "dense_rank", "ntile", "lag", "lead", "first_value", "last_value", "nth_value", "cume_dist", "percent_rank",
}
MAX_RANDOMBLOB_BYTES = 1_000_000


@dataclass
class AccessPolicy:
    """What SQL may reference. `tables` maps lower-case table name -> {lower-case column: type}."""
    dialect: str  # "sqlite" | "postgres"
    tables: dict[str, dict[str, str]]
    restricted: dict[str, set[str]] = field(default_factory=dict)  # table -> restricted columns
    schema: str | None = None  # allowed schema qualifier (postgres "bi"); None = unqualified only
    max_sql_chars: int = 20_000
    max_depth: int = 64
    max_joins: int = 16

    def restricted_columns(self, table: str) -> set[str]:
        return self.restricted.get(table.lower(), set())

    @property
    def all_restricted_names(self) -> set[str]:
        return {c for cols in self.restricted.values() for c in cols}


def is_system_catalog(table: str, schema: str | None) -> bool:
    if schema and schema.lower() in SYSTEM_CATALOG_SCHEMAS:
        return True
    name = table.lower()
    return name in SYSTEM_CATALOG_TABLES or bool(SYSTEM_TABLE_PREFIX.match(name))


def is_denied_function(name: str) -> bool:
    return any(p.search(name) for p in DENIED_FUNCTION_PATTERNS)
