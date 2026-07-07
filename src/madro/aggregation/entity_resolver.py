class EntityResolver:
    """Joins per-artifact structured records (persisted in job_artifact.provenance_details)
    into entities by a shared key."""

    def resolve(
        self, lex_by_artifact: dict[str, tuple[float, list[dict]]]
    ) -> dict[str, tuple[list[str], dict]]:
        parsed: dict[str, list[dict]] = {
            artifact_id: records for artifact_id, (_, records) in lex_by_artifact.items()
        }

        # Find common keys across all record sets
        all_key_sets = []
        for records in parsed.values():
            if records:
                all_key_sets.append(set(records[0].keys()))

        common_keys = set.intersection(*all_key_sets) if all_key_sets else set()
        # Prefer known entity keys for joining
        join_key = None
        for candidate in ("profile_id", "id", "entity_id", "account_id"):
            if candidate in common_keys:
                join_key = candidate
                break

        entities: dict[str, tuple[list[str], dict]] = {}

        if join_key:
            # Group records by join key across artifacts
            for artifact_id, records in parsed.items():
                for record in records:
                    eid = str(record.get(join_key, artifact_id))
                    if eid not in entities:
                        entities[eid] = ([artifact_id], record)
                    else:
                        if artifact_id not in entities[eid][0]:
                            entities[eid][0].append(artifact_id)
                        entities[eid] = (entities[eid][0], {**entities[eid][1], **record})
        else:
            # No join key found — each record is its own entity (an artifact may
            # hold many rows, e.g. all publications matched by one agent call)
            for artifact_id, records in parsed.items():
                for idx, record in enumerate(records):
                    entities[f"{artifact_id}:{idx}"] = ([artifact_id], record)

        return entities
