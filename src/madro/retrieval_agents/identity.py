from __future__ import annotations

from pydantic import BaseModel


class EntityRef(BaseModel):
    """Points at the field in a retrieval agent's output record that carries an
    entity's identifier, and what kind of entity it identifies (e.g. "profile",
    "publication") — EntityResolver groups records into entities by matching
    (kind, value) pairs across artifacts, instead of guessing from field names.

    `fallback`: for a record whose "natural" identity (e.g. a comment's own
    publication_id) may or may not already be an entity elsewhere in the
    thread — EntityResolver merges into the primary identity if that entity
    exists, otherwise resolves the record under `fallback` instead of forcing
    it into an entity nothing else in the thread produced. See
    docs/adr/0005-conditional-collapse-for-comment-identity.md."""
    field: str
    kind: str
    fallback: EntityRef | None = None
