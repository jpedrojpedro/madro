from dataclasses import dataclass, field


@dataclass
class ResolvedEntity:
    """One entity EntityResolver joined records into — every artifact that
    contributed a record to it, and the merged record data itself."""
    artifact_ids: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class RankedEntity:
    entity_id: str
    entity_data: dict
    s_lex: float
    s_sem: float
    s_relevance: float
    # The sub-demand(s) whose ranking pool surfaced this entity into the
    # top-k — usually one, more than one if the same real-world entity was
    # resolved from artifacts belonging to different sub-demands. Drives
    # ResponseSynthesisAgent's per-sub-demand evidence grouping.
    sub_demands: list[str] = field(default_factory=list)
