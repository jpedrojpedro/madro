from pydantic import BaseModel


class EntityRef(BaseModel):
    """Points at the field in a retrieval agent's output record that carries an
    entity's identifier, and what kind of entity it identifies (e.g. "profile",
    "publication") — EntityResolver groups records into entities by matching
    (kind, value) pairs across artifacts, instead of guessing from field names."""
    field: str
    kind: str
