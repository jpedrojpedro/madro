from dataclasses import dataclass


@dataclass
class RankedEntity:
    entity_id: str
    entity_data: dict
    s_lex: float
    s_sem: float
    s_relevance: float
