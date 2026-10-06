"""
Builds a per-agent scoped schema doc for on-the-fly SQL retrieval agents, as
literal DDL instead of schema_scope.py's annotated Markdown — for use with a
model (e.g. Arctic-Text2SQL-R1-7B) trained on literal `CREATE TABLE`
statements rather than prose schema descriptions.

Parses `configs/dowser_schema.sql` (a `pg_dump --schema-only`-style dump) by
top-level statement, keeping only `CREATE TABLE` and `CREATE TYPE ... AS
ENUM` — `alter table/type ... owner to`, `create index`, and `create
function` (the UUID-extension/trigger internals) are dropped as noise
irrelevant to writing a retrieval query. Same `public.*`-only scoping intent
as schema_scope.py: this only ever describes the dowser domain tables, never
`benchmark_hints.*`.
"""

import re
from pathlib import Path

from django.conf import settings

DOWSER_SCHEMA_SQL_PATH = Path(settings.BASE_DIR) / "configs" / "dowser_schema.sql"

_TABLE_RE = re.compile(r"^\s*create\s+table\s+(\w+)", re.IGNORECASE)
_ENUM_RE = re.compile(r"^\s*create\s+type\s+\w+\s+as\s+enum", re.IGNORECASE)


def _split_statements(sql: str) -> list[str]:
    """Splits on top-level `;`, treating anything between a pair of `$$` as
    opaque (function bodies contain their own `;`s that must not split)."""
    statements: list[str] = []
    current: list[str] = []
    in_dollar_quote = False
    i, n = 0, len(sql)
    while i < n:
        if sql[i : i + 2] == "$$":
            in_dollar_quote = not in_dollar_quote
            current.append("$$")
            i += 2
            continue
        char = sql[i]
        if char == ";" and not in_dollar_quote:
            current.append(char)
            statements.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(char)
        i += 1
    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return [s for s in statements if s]


def _parse(sql: str) -> tuple[dict[str, str], list[str]]:
    tables: dict[str, str] = {}
    enums: list[str] = []
    for statement in _split_statements(sql):
        table_match = _TABLE_RE.match(statement)
        if table_match:
            tables[table_match.group(1)] = statement
            continue
        if _ENUM_RE.match(statement):
            enums.append(statement)
    return tables, enums


_TABLE_DDL, _ENUM_DDL = _parse(DOWSER_SCHEMA_SQL_PATH.read_text())


def build_scoped_schema_ddl(tables: list[str]) -> str:
    """All enum type definitions (always included — small in count, and
    exactly the domain vocabulary a DDL-trained model needs for e.g. `WHERE
    inferred_category = '...'`) followed by the `CREATE TABLE` statement for
    each bare table name in `tables`, in the order given, deduplicating
    repeats. Raises on an unknown table, same contract as
    schema_scope.build_scoped_schema()."""
    parts = list(_ENUM_DDL)
    seen: set[str] = set()
    for table in tables:
        if table in seen:
            continue
        seen.add(table)

        statement = _TABLE_DDL.get(table)
        if statement is None:
            raise ValueError(f"No DDL found for table {table!r}")
        parts.append(statement)

    return "\n\n".join(parts)
