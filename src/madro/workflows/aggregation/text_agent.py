"""
Aggregation Text Agent.

Computes S_t(e) = α · S_lex(e) + β · S_sem(e) for candidate entities
produced by text-based Fetcher Agents.
"""

import json
from dataclasses import dataclass

from madro.config import load_config
from madro.db import async_cursor
from madro.workflows.normalizer import _get_encoder


@dataclass
class ScoredEntity:
    entity_id: str
    entity_data: dict
    s_lex: float
    s_sem: float
    s_text: float


def _embed_query(query: str) -> list[float]:
    encoder = _get_encoder()
    return encoder.encode(query).tolist()


async def aggregate_text(thread_id: str, demand: str) -> list[ScoredEntity]:
    """Compute text aggregation scores for all candidate entities in a thread."""
    cfg = load_config()
    alpha = cfg.fusion.text.alpha
    beta = cfg.fusion.text.beta

    query_embedding = _embed_query(demand)
    vector_literal = "[" + ",".join(map(str, query_embedding)) + "]"

    async with async_cursor() as cur:
        # Lexical scores per artifact
        await cur.execute(
            """
            SELECT ja.job_status_id,
                   ja.canonical_text,
                   ts_rank(ja.lexical_vector, plainto_tsquery('english', %s), 32) AS lex_score
            FROM broker.job_artifact ja
            JOIN broker.job_status js ON js.id = ja.job_status_id
            JOIN broker.job_execution je ON je.job_id = js.job_id AND je.agent_id = js.agent_id
            WHERE je.thread_id = %s
              AND js.status = 'completed'
            """,
            [demand, thread_id],
        )
        lex_rows = await cur.fetchall()

        # Semantic scores per chunk
        await cur.execute(
            """
            SELECT jad.job_artifact_id,
                   1 - (jad.embedding <=> %s::vector) AS sem_score
            FROM broker.job_artifact_document jad
            JOIN broker.job_artifact ja ON ja.job_status_id = jad.job_artifact_id
            JOIN broker.job_status js ON js.id = ja.job_status_id
            JOIN broker.job_execution je ON je.job_id = js.job_id AND je.agent_id = js.agent_id
            WHERE je.thread_id = %s
            """,
            [vector_literal, thread_id],
        )
        sem_rows = await cur.fetchall()

    # Group lexical scores by artifact
    lex_by_artifact: dict[str, tuple[float, str]] = {}
    for row in lex_rows:
        artifact_id, canonical_text, lex_score = row
        lex_by_artifact[str(artifact_id)] = (float(lex_score), canonical_text)

    # Group semantic scores by artifact — best chunk score per artifact
    sem_by_artifact: dict[str, float] = {}
    for row in sem_rows:
        artifact_id, sem_score = row
        key = str(artifact_id)
        sem_by_artifact[key] = max(sem_by_artifact.get(key, 0.0), float(sem_score))

    # Try to resolve entities by joining on common keys across artifacts
    entities = _resolve_entities(lex_by_artifact)

    results: list[ScoredEntity] = []
    for entity_id, (artifact_ids, entity_data) in entities.items():
        s_lex = max(lex_by_artifact[aid][0] for aid in artifact_ids if aid in lex_by_artifact)
        s_sem = max(sem_by_artifact.get(aid, 0.0) for aid in artifact_ids)
        s_text = alpha * s_lex + beta * s_sem
        results.append(ScoredEntity(
            entity_id=entity_id,
            entity_data=entity_data,
            s_lex=s_lex,
            s_sem=s_sem,
            s_text=s_text,
        ))

    results.sort(key=lambda e: e.s_text, reverse=True)
    return results


def _resolve_entities(
    lex_by_artifact: dict[str, tuple[float, str]],
) -> dict[str, tuple[list[str], dict]]:
    """
    Attempt to join artifacts into entities by matching keys in their canonical JSON.
    Falls back to treating each artifact as its own entity.
    """
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
