"""
Image aggregation.

aggregate_image():
  Pure vector scoring against stored embeddings — symmetric with aggregate_text.
  S_i(e) = γ · S_vis(e) + δ · S_sem_i(e)
"""

import json

import numpy as np

from madro.config import load_config
from madro.db import async_cursor
from madro.workflows.normalizer import MultimodalNormalizer



async def aggregate_image(thread_id: str, demand: str) -> dict[str, float]:
    """Return S_i scores keyed by publication_id — pure vector scoring against stored embeddings."""
    cfg = load_config()
    gamma = cfg.fusion.image.gamma
    delta = cfg.fusion.image.delta

    encoder = MultimodalNormalizer()._get_encoder()
    query_embedding = encoder.encode(demand).tolist()
    vector_literal = "[" + ",".join(map(str, query_embedding)) + "]"

    async with async_cursor() as cur:
        await cur.execute(
            """
            SELECT jad.job_artifact_id,
                   1 - (jad.embedding <=> %s::vector) AS sem_score,
                   ja.canonical_text
            FROM broker.job_artifact_document jad
            JOIN broker.job_artifact ja ON ja.job_status_id = jad.job_artifact_id
            JOIN broker.job_status js ON js.id = ja.job_status_id
            JOIN broker.job_execution je ON je.job_id = js.job_id AND je.agent_id = js.agent_id
            JOIN agents_topics.agent a ON a.id = je.agent_id
            WHERE je.thread_id = %s
              AND js.status = 'completed'
              AND a.modality = 'image'
            """,
            [vector_literal, thread_id],
        )
        rows = await cur.fetchall()

    if not rows:
        return {}

    scores: dict[str, float] = {}

    for row in rows:
        _, sem_score, canonical_text = row
        try:
            records = json.loads(canonical_text)
            if not isinstance(records, list):
                records = [records]
        except (json.JSONDecodeError, TypeError):
            continue
        for record in records:
            pub_id = str(record.get("publication_id", ""))
            s_image = gamma * float(sem_score) + delta * float(sem_score)
            scores[pub_id] = max(scores.get(pub_id, 0.0), s_image)

    return scores
