"""
Aggregation Image Agent.

For each image artifact in the thread:
  1. Decode bytea → base64
  2. Call vision model to get a text description
  3. Embed the description with the shared text encoder
  4. Compute S_i(e) = γ · S_vis(e) + δ · S_sem_i(e)
     where S_vis is the cosine similarity between the image description
     embedding and the query embedding.
"""

import json
from dataclasses import dataclass

from openai import AsyncAzureOpenAI
import os
import numpy as np

from madro.config import load_config
from madro.db import async_cursor
from madro.workflows.normalizer import _get_encoder


@dataclass
class ScoredImageEntity:
    entity_id: str
    s_image: float


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


_IMAGE_MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp", "gif": "image/gif", "mp4": "image/jpeg"}


async def _describe_image(client: AsyncAzureOpenAI, model: str, b64: str, extension: str) -> str:
    mime = _IMAGE_MIME.get(extension, "image/jpeg")
    response = await client.chat.completions.create(
        model=model,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                },
                {
                    "type": "text",
                    "text": "Extract all useful information from this image: visible text, product names, prices, location details, business name, and describe the setting.",
                },
            ],
        }],
        max_tokens=512,
    )
    return response.choices[0].message.content


async def aggregate_image(thread_id: str, demand: str) -> dict[str, float]:
    """Return S_i scores keyed by publication_id for all image artifacts in the thread."""
    cfg = load_config()
    gamma = cfg.fusion.image.gamma
    delta = cfg.fusion.image.delta

    async with async_cursor() as cur:
        await cur.execute(
            """
            SELECT ja.job_status_id, ja.canonical_text
            FROM broker.job_artifact ja
            JOIN broker.job_status js ON js.id = ja.job_status_id
            JOIN broker.job_execution je ON je.job_id = js.job_id AND je.agent_id = js.agent_id
            JOIN agents_topics.agent a ON a.id = je.agent_id
            WHERE je.thread_id = %s
              AND js.status = 'completed'
              AND a.modality = 'image'
            """,
            [thread_id],
        )
        rows = await cur.fetchall()

    if not rows:
        return {}

    client = AsyncAzureOpenAI(
        azure_endpoint=os.environ["AZURE_ENDPOINT"],
        api_version=os.environ.get("API_VERSION", "2025-04-01-preview"),
        api_key=os.environ["API_KEY"],
    )
    encoder = _get_encoder()
    query_embedding = encoder.encode(demand).tolist()

    scores: dict[str, float] = {}

    for _, canonical_text in rows:
        try:
            records = json.loads(canonical_text)
            if not isinstance(records, list):
                records = [records]
        except (json.JSONDecodeError, TypeError):
            continue

        for record in records:
            raw_data = record.get("data")
            if not raw_data:
                continue

            publication_id = str(record.get("publication_id", ""))
            # FIXME: record["extension"] can be `jpg` or `mp4`
            #  but although it says `mp4`, it is only a single frame
            #  of the video; Thus, let's hard code as `jpg`.
            #  extension = record.get("extension", "jpg")
            extension = "jpg"

            description = await _describe_image(client, cfg.model.name, raw_data, extension)
            desc_embedding = encoder.encode(description).tolist()

            s_vis = _cosine(query_embedding, desc_embedding)
            s_sem_i = _cosine(query_embedding, desc_embedding)  # same space — reuse
            s_image = gamma * s_vis + delta * s_sem_i

            scores[publication_id] = max(scores.get(publication_id, 0.0), s_image)

    return scores
