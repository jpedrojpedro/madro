class EntityResolver:
    """Joins per-artifact structured records (persisted in job_artifact.provenance_details)
    into entities using each artifact's own declared identity field (retrieval_agents/identity.py),
    rather than one key guessed by intersecting field names across every artifact in the thread."""

    def resolve(
        self, lex_by_artifact: dict[str, tuple[float, dict | None, list[dict]]]
    ) -> dict[str, tuple[list[str], dict]]:
        entities: dict[str, tuple[list[str], dict]] = {}

        for artifact_id, (_, identity, records) in lex_by_artifact.items():
            for idx, record in enumerate(records):
                if identity and record.get(identity["field"]) is not None:
                    eid = f"{identity['kind']}:{record[identity['field']]}"
                else:
                    # No declared identity (or missing on this record) — it stands
                    # as its own entity, same as an agent with no identity at all.
                    eid = f"{artifact_id}:{idx}"

                if eid not in entities:
                    entities[eid] = ([artifact_id], dict(record))
                    continue
                artifact_ids, merged = entities[eid]
                if artifact_id not in artifact_ids:
                    artifact_ids.append(artifact_id)
                entities[eid] = (artifact_ids, {**merged, **record})

        return entities
