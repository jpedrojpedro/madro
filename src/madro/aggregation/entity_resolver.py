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
    ) -> dict[str, tuple[list[str], dict]]:
        entities: dict[str, tuple[list[str], dict]] = {}
        deferred: list[tuple[str, dict, dict, int]] = []

        for artifact_id, (identity, records) in records_by_artifact.items():
            for idx, record in enumerate(records):
                if identity and identity.get("fallback"):
                    deferred.append((artifact_id, identity, record, idx))
                    continue
                self._merge(entities, artifact_id, self._entity_id(identity, record, artifact_id, idx), record)

        for artifact_id, identity, record, idx in deferred:
            primary_eid = self._entity_id(identity, record, artifact_id, idx)
            eid = primary_eid if primary_eid in entities else self._entity_id(
                identity["fallback"], record, artifact_id, idx
            )
            self._merge(entities, artifact_id, eid, record)

        return entities

    @staticmethod
    def _entity_id(identity: dict | None, record: dict, artifact_id: str, idx: int) -> str:
        if identity and record.get(identity["field"]) is not None:
            return f"{identity['kind']}:{record[identity['field']]}"
        # No declared identity (or missing on this record) — it stands as its
        # own entity, same as an agent with no identity at all.
        return f"{artifact_id}:{idx}"

    @staticmethod
    def _merge(entities: dict[str, tuple[list[str], dict]], artifact_id: str, eid: str, record: dict) -> None:
        if eid not in entities:
            entities[eid] = ([artifact_id], dict(record))
            return
        artifact_ids, merged = entities[eid]
        if artifact_id not in artifact_ids:
            artifact_ids.append(artifact_id)
        merged_record = {**merged, **record}
        # A plain {**merged, **record} spread would let whichever record
        # merges in last silently overwrite rnk (each agent's own
        # retrieval-time ts_rank score, reused as-is for s_lex — see
        # RelevanceRanker) — keep the max seen across every record
        # contributing to this entity instead of an arbitrary one.
        if "rnk" in merged and "rnk" in record:
            merged_record["rnk"] = max(merged["rnk"], record["rnk"])
        entities[eid] = (artifact_ids, merged_record)
