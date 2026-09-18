from dataclasses import dataclass

from madro.aggregation.entities import ResolvedEntity


@dataclass
class _DeferredRecord:
    """A record whose identity declares a `fallback` — held until every
    non-fallback artifact has resolved (see EntityResolver.resolve)."""
    artifact_id: str
    identity: dict
    record: dict
    idx: int


class EntityResolver:
    """Joins per-artifact structured records (persisted in job_artifact.provenance_details)
    into entities using each artifact's own declared identity field (retrieval_agents/identity.py),
    rather than one key guessed by intersecting field names across every artifact in the thread.

    An identity that declares a `fallback` is resolved in a second pass, after
    every other artifact's entities already exist: if the primary identity
    (e.g. a comment's publication_id) already has an entity — some other
    artifact in the thread produced one — the record merges into it, same as
    always. Otherwise it resolves under `fallback` (e.g. comment_id) instead
    of being forced into a publication entity nothing else in the thread
    surfaced. See docs/adr/0005-conditional-collapse-for-comment-identity.md."""

    def resolve(
        self, records_by_artifact: dict[str, tuple[dict | None, list[dict]]]
    ) -> dict[str, ResolvedEntity]:
        entities: dict[str, ResolvedEntity] = {}
        deferred = self._merge_direct_records(entities, records_by_artifact)
        self._merge_deferred_records(entities, deferred)
        return entities

    def _merge_direct_records(
        self,
        entities: dict[str, ResolvedEntity],
        records_by_artifact: dict[str, tuple[dict | None, list[dict]]],
    ) -> list[_DeferredRecord]:
        """First pass: merges every record whose identity has no `fallback`
        right away, setting aside fallback-capable ones (see class docstring)
        instead of resolving them yet."""
        deferred: list[_DeferredRecord] = []
        for artifact_id, (identity, records) in records_by_artifact.items():
            for idx, record in enumerate(records):
                if identity and identity.get("fallback"):
                    deferred.append(_DeferredRecord(artifact_id, identity, record, idx))
                    continue
                self._merge(entities, artifact_id, self._entity_id(identity, record, artifact_id, idx), record)
        return deferred

    def _merge_deferred_records(
        self, entities: dict[str, ResolvedEntity], deferred: list[_DeferredRecord]
    ) -> None:
        """Second pass: now that every non-fallback artifact's entities
        exist, each deferred record merges into its primary identity's
        entity if one was produced, otherwise resolves under its fallback."""
        for d in deferred:
            primary_eid = self._entity_id(d.identity, d.record, d.artifact_id, d.idx)
            eid = primary_eid if primary_eid in entities else self._entity_id(
                d.identity["fallback"], d.record, d.artifact_id, d.idx
            )
            self._merge(entities, d.artifact_id, eid, d.record)

    @staticmethod
    def _entity_id(identity: dict | None, record: dict, artifact_id: str, idx: int) -> str:
        if identity and record.get(identity["field"]) is not None:
            return f"{identity['kind']}:{record[identity['field']]}"
        # No declared identity (or missing on this record) — it stands as its
        # own entity, same as an agent with no identity at all.
        return f"{artifact_id}:{idx}"

    @staticmethod
    def _merge(entities: dict[str, ResolvedEntity], artifact_id: str, eid: str, record: dict) -> None:
        if eid not in entities:
            entities[eid] = ResolvedEntity(artifact_ids=[artifact_id], data=dict(record))
            return
        entity = entities[eid]
        if artifact_id not in entity.artifact_ids:
            entity.artifact_ids.append(artifact_id)
        merged_data = {**entity.data, **record}
        # A plain {**entity.data, **record} spread would let whichever record
        # merges in last silently overwrite rnk (each agent's own
        # retrieval-time ts_rank score, reused as-is for s_lex — see
        # RelevanceRanker) — keep the max seen across every record
        # contributing to this entity instead of an arbitrary one.
        if "rnk" in entity.data and "rnk" in record:
            merged_data["rnk"] = max(entity.data["rnk"], record["rnk"])
        entity.data = merged_data
