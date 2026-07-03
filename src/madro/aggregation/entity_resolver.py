import json


class EntityResolver:
    """Joins per-artifact canonical JSON records into entities by a shared key."""

    def resolve(
        self, lex_by_artifact: dict[str, tuple[float, str]]
    ) -> dict[str, tuple[list[str], dict]]:
        parsed: dict[str, list[dict]] = {}
        for artifact_id, (_, canonical_text) in lex_by_artifact.items():
            try:
                data = json.loads(canonical_text)
                if isinstance(data, list):
                    parsed[artifact_id] = data
                else:
                    parsed[artifact_id] = [data]
            except (json.JSONDecodeError, TypeError):
                parsed[artifact_id] = [{"_raw": canonical_text}]

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
            # No join key found — each artifact is its own entity
            for artifact_id, records in parsed.items():
                entities[artifact_id] = ([artifact_id], records[0] if records else {})

        return entities
