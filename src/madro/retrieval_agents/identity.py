from pydantic import BaseModel


class EntityRef(BaseModel):
    """Points at the field in a retrieval agent's output record that carries an
    entity's identifier, and what kind of entity it identifies (e.g. "profile",
    "publication") — EntityResolver groups records into entities by matching
    (kind, value) pairs across artifacts, instead of guessing from field names."""
    field: str
    kind: str


class Relationship(BaseModel):
    """Declares that a record represents an edge between two entities (e.g.
    a `follower_profile_id` profile `follows` a `profile_id` profile) rather
    than being about a single entity. Both sides are still joined into their
    respective entities; the predicate is retained as evidence rather than
    used for grouping."""
    subject: EntityRef
    predicate: str
    object: EntityRef


def flatten_refs(identity: list[dict]) -> list[tuple[str, str]]:
    """Expands an agent's declared identity (mix of plain refs and relationships,
    as persisted in provenance_details) into a flat list of (field, kind) pairs."""
    refs: list[tuple[str, str]] = []
    for item in identity:
        if "predicate" in item:
            refs.append((item["subject"]["field"], item["subject"]["kind"]))
            refs.append((item["object"]["field"], item["object"]["kind"]))
        else:
            refs.append((item["field"], item["kind"]))
    return refs
