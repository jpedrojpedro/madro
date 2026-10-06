"""
Builds a per-agent scoped schema doc for on-the-fly SQL retrieval agents:
only the `public.*` table sections a given agent actually needs, pulled out
of `configs/dowser_schema.md`. Deliberately has no access to
`benchmark_hints.*` at all — those materialized views exist only for Ground
Truth's use;
RetrievalAgents must work from the same public schema a real, un-hinted
agent would see.
"""

from madro.sql_generation import DOWSER_SCHEMA_PATH, parse_schema_sections

PUBLIC_SECTIONS = parse_schema_sections(DOWSER_SCHEMA_PATH.read_text())


def build_scoped_schema(tables: list[str]) -> str:
    """Concatenates the `public.<table>` schema section for each bare table
    name in `tables` (e.g. "profile", "publication_collab"), in the order
    given, deduplicating repeats. Raises on an unknown table — a silently
    empty/partial prompt would be worse than a loud failure here."""
    seen: set[str] = set()
    parts: list[str] = []
    for table in tables:
        if table in seen:
            continue
        seen.add(table)

        section = PUBLIC_SECTIONS.get(table)
        if section is None:
            raise ValueError(f"No schema section found for table {table!r}")
        parts.append(section)

    return "\n\n".join(parts)
