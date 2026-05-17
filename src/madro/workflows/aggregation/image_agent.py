"""
Aggregation Image Agent.

For each image artifact in the thread:
  1. Read image records from provenance_details
  2. Use BLIP (HuggingFace) to get a text description
  3. Embed the description with the shared text encoder
  4. Compute S_i(e) = γ · S_vis(e) + δ · S_sem_i(e)
"""

import asyncio
import base64
import json
from functools import partial
import io

import numpy as np
from PIL import Image
from transformers import BlipProcessor, BlipForConditionalGeneration

from madro.config import load_config
from madro.db import async_cursor
from madro.workflows.normalizer import _get_encoder, _CACHE_DIR


_blip_processor: BlipProcessor | None = None
_blip_model: BlipForConditionalGeneration | None = None
_BLIP_MODEL = "Salesforce/blip-image-captioning-base"


def _get_blip():
    global _blip_processor, _blip_model
    if _blip_processor is None:
        _blip_processor = BlipProcessor.from_pretrained(_BLIP_MODEL, cache_dir=str(_CACHE_DIR))
        _blip_model = BlipForConditionalGeneration.from_pretrained(_BLIP_MODEL, cache_dir=str(_CACHE_DIR))
    return _blip_processor, _blip_model


def _describe_sync(b64: str) -> str:
    processor, model = _get_blip()
    image = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    inputs = processor(image, return_tensors="pt")
    out = model.generate(**inputs, max_new_tokens=128)
    return processor.decode(out[0], skip_special_tokens=True)


def _cosine(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


async def aggregate_image(thread_id: str, demand: str) -> dict[str, float]:
    """Return S_i scores keyed by publication_id for all image artifacts in the thread."""
    cfg = load_config()
    gamma = cfg.fusion.image.gamma
    delta = cfg.fusion.image.delta

    async with async_cursor() as cur:
        await cur.execute(
            """
            SELECT ja.provenance_details
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
        return {}, {}

    encoder = _get_encoder()
    loop = asyncio.get_event_loop()
    query_embedding = encoder.encode(demand).tolist()
    scores: dict[str, float] = {}
    descriptions: dict[str, str] = {}

    for (provenance,) in rows:
        images = provenance.get("images", []) if isinstance(provenance, dict) else []
        for img in images:
            b64 = img.get("data")
            if not b64:
                continue
            publication_id = str(img.get("publication_id", ""))
            description = await loop.run_in_executor(None, partial(_describe_sync, b64))
            desc_embedding = encoder.encode(description).tolist()
            s_vis = _cosine(query_embedding, desc_embedding)
            s_sem_i = _cosine(query_embedding, desc_embedding)
            s_image = gamma * s_vis + delta * s_sem_i
            if s_image > scores.get(publication_id, 0.0):
                scores[publication_id] = s_image
                descriptions[publication_id] = description

    return scores, descriptions
