"""
Hand-authored, per-agent scoped schema docs for Gemini's on-the-fly SQL
generation — see docs/adr/0007-static-per-agent-schema-docs-for-gemini-resolver.md.
Loaded once at import time, keyed by filename, so `RetrievalAgent.SCHEMA_DOC`
can name a file without re-reading it on every job execution.
"""

from pathlib import Path

SCHEMAS_DIR = Path(__file__).parent / "schemas"

SCHEMA_DOCS: dict[str, str] = {path.name: path.read_text() for path in SCHEMAS_DIR.glob("*.md")}
