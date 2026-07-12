from madro.retrieval_agents.identity import flatten_refs


class EntityResolver:
    """Joins per-artifact structured records (persisted in job_artifact.provenance_details)
    into entities using each artifact's own declared identity (retrieval_agents/identity.py),
    rather than one key intersected across every artifact in the thread — a single
    artifact whose records carry a different identity field no longer blocks joining
    for every other artifact."""

    def resolve(
        self, lex_by_artifact: dict[str, tuple[float, list[dict], list[dict]]]
    ) -> dict[str, tuple[list[str], dict]]:
        entities: dict[str, tuple[list[str], dict]] = {}

        def merge(eid: str, artifact_id: str, record: dict) -> None:
            if eid not in entities:
                entities[eid] = ([artifact_id], dict(record))
                return
            artifact_ids, merged = entities[eid]
            if artifact_id not in artifact_ids:
                artifact_ids.append(artifact_id)
            entities[eid] = (artifact_ids, {**merged, **record})

        for artifact_id, (_, identity, records) in lex_by_artifact.items():
            refs = flatten_refs(identity)
            relationships = [item for item in identity if "predicate" in item]

            for idx, record in enumerate(records):
                matched = [(field, kind) for field, kind in refs if record.get(field) is not None]

                if not matched:
                    # No declared identity (or none of it present on this record) —
                    # it stands as its own entity, same as an agent with no identity at all.
                    merge(f"{artifact_id}:{idx}", artifact_id, record)
                    continue

                for field, kind in matched:
                    merge(f"{kind}:{record[field]}", artifact_id, record)

                # A record satisfying both sides of a relationship feeds two distinct
                # entities (e.g. a "follows" edge merges into both the follower's and
                # the followed profile's entity) — record that connection as evidence
                # on both without inventing a third "edge" entity for it.
                for rel in relationships:
                    s_field, s_kind = rel["subject"]["field"], rel["subject"]["kind"]
                    o_field, o_kind = rel["object"]["field"], rel["object"]["kind"]
                    if record.get(s_field) is None or record.get(o_field) is None:
                        continue
                    s_eid = f"{s_kind}:{record[s_field]}"
                    o_eid = f"{o_kind}:{record[o_field]}"
                    entities[s_eid][1].setdefault("_relationships", []).append(
                        {"predicate": rel["predicate"], "role": "subject", "with": o_eid}
                    )
                    entities[o_eid][1].setdefault("_relationships", []).append(
                        {"predicate": rel["predicate"], "role": "object", "with": s_eid}
                    )

        return entities
