"""
Builds a per-question, hint-scoped schema doc for Ground Truth SQL
generation: only the tables/views a question's `hint` array names, pulled
out of `configs/dowser_schema.md` (`public.*`) and `benchmark_hints/schema.md`
(`benchmark_hints.*`) docs — never the whole schema, so Ground Truth's
system prompt stays as small as the question actually needs. Baseline and
MADRO never call this, so their table choices stay unsteered. RetrievalAgents
use their own public.*-only equivalent instead — see
`madro.retrieval_agents.schema_scope` — which has no access to
`benchmark_hints.*` at all.
"""

import re
from pathlib import Path

from madro.retrieval_agents.schema_scope import PUBLIC_SECTIONS as _PUBLIC_SECTIONS
from madro.sql_generation import parse_schema_sections

_BENCHMARK_HINTS_SCHEMA = (Path(__file__).parent.parent / "benchmark_hints" / "schema.md").read_text()

# benchmark_hints/schema.md documents the per-account publication/comment
# views once, generically, under a literal "<account>_publication" /
# "<account>_comment" heading rather than once per account — these patterns
# route a concrete hint like "haight_clothing_publication" to that shared
# section.
_ACCOUNT_PUBLICATION_RE = re.compile(r".+_publication$")
_ACCOUNT_COMMENT_RE = re.compile(r".+_comment$")

_BENCHMARK_HINTS_SECTIONS = parse_schema_sections(_BENCHMARK_HINTS_SCHEMA)


def _benchmark_hints_section(table: str) -> str | None:
    if table == "profile":
        return _BENCHMARK_HINTS_SECTIONS.get("benchmark_hints.profile")
    if table == "profile_location":
        return _BENCHMARK_HINTS_SECTIONS.get("benchmark_hints.profile_location")
    if _ACCOUNT_PUBLICATION_RE.match(table):
        section = _BENCHMARK_HINTS_SECTIONS.get("benchmark_hints.<account>_publication")
        return section.replace("<account>_publication", table) if section else None
    if _ACCOUNT_COMMENT_RE.match(table):
        section = _BENCHMARK_HINTS_SECTIONS.get("benchmark_hints.<account>_comment")
        return section.replace("<account>_comment", table) if section else None
    return None


def build_hint_schema(hints: list[str]) -> str:
    """Concatenates the schema section for each qualified table name in
    `hints` (e.g. "public.profile", "benchmark_hints.haight_clothing_comment"),
    in the order given, deduplicating repeats. Raises on a hinted table with
    no matching section — a silently empty/partial prompt would be worse
    than a loud failure here."""
    seen: set[str] = set()
    parts: list[str] = []
    for hint in hints:
        if hint in seen:
            continue
        seen.add(hint)

        schema, _, table = hint.partition(".")
        if schema == "public":
            section = _PUBLIC_SECTIONS.get(table)
        elif schema == "benchmark_hints":
            section = _benchmark_hints_section(table)
        else:
            section = None

        if section is None:
            raise ValueError(f"No schema section found for hinted table {hint!r}")
        parts.append(section)

    return "\n\n".join(parts)
