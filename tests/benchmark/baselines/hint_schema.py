"""
Builds a per-question, hint-scoped schema doc for Ground Truth SQL
generation: only the tables/views a question's `hint` array names, pulled
out of the full `dowser_schema.md` (`public.*`) and
`benchmark_hints/schema.md` (`benchmark_hints.*`) docs — never the whole
schema, so Ground Truth's system prompt stays as small as the question
actually needs. Baseline and MADRO never call this; see CONTEXT.md's `hint`
entry for why.
"""

import re
from pathlib import Path

_DOWSER_SCHEMA = (Path(__file__).parent / "dowser_schema.md").read_text()
_BENCHMARK_HINTS_SCHEMA = (Path(__file__).parent.parent / "benchmark_hints" / "schema.md").read_text()

# benchmark_hints/schema.md documents the per-account publication/comment
# views once, generically, under a literal "<account>_publication" /
# "<account>_comment" heading rather than once per account — these patterns
# route a concrete hint like "haight_clothing_publication" to that shared
# section.
_ACCOUNT_PUBLICATION_RE = re.compile(r".+_publication$")
_ACCOUNT_COMMENT_RE = re.compile(r".+_comment$")


def _sections(markdown: str) -> dict[str, str]:
    """`## \\`name\\`` heading text (backtick contents, verbatim) -> that section's body."""
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_name is not None:
            sections[current_name] = "\n".join(current_lines).strip()

    for line in markdown.splitlines():
        if line.startswith("## "):
            _flush()
            match = re.search(r"`([^`]+)`", line)
            current_name = match.group(1) if match else None
            current_lines = [line]
        else:
            current_lines.append(line)
    _flush()
    return sections


_PUBLIC_SECTIONS = _sections(_DOWSER_SCHEMA)
_BENCHMARK_HINTS_SECTIONS = _sections(_BENCHMARK_HINTS_SCHEMA)


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
