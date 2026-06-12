"""
Cross-modality Fusion.

Computes S_fusion(e) = w_t · S_t(e) + w_i · S_i(e)
Currently only text modality is implemented.
"""

import asyncio

from dataclasses import dataclass

from madro.config import load_config
from madro.workflows.aggregation.text_agent import ScoredEntity, aggregate_text
from madro.workflows.aggregation.image_agent import aggregate_image


@dataclass
class FusedEntity:
    entity_id: str
    entity_data: dict
    s_text: float
    s_image: float
    s_fusion: float


async def fuse(thread_id: str, demand: str) -> list[FusedEntity]:
    """Rank candidate entities by fused score across modalities."""
    cfg = load_config()
    w_t = cfg.fusion.modality_weights.w_t
    w_i = cfg.fusion.modality_weights.w_i

    text_scores, image_scores = await asyncio.gather(
        aggregate_text(thread_id, demand),
        aggregate_image(thread_id, demand),
    )

    results: dict[str, FusedEntity] = {}

    for entity in text_scores:
        # try to match image score by publication_id if present in entity data
        pub_id = str(entity.entity_data.get("publication_id", ""))
        s_image = image_scores.get(pub_id) or image_scores.get(entity.entity_id, 0.0)
        s_fusion = w_t * entity.s_text + w_i * s_image
        results[entity.entity_id] = FusedEntity(
            entity_id=entity.entity_id,
            entity_data=entity.entity_data,
            s_text=entity.s_text,
            s_image=s_image,
            s_fusion=s_fusion,
        )

    # surface image-only entities that had no matching text entity
    for pub_id, s_image in image_scores.items():
        if pub_id not in results:
            s_fusion = w_i * s_image
            results[pub_id] = FusedEntity(
                entity_id=pub_id,
                entity_data={"publication_id": pub_id},
                s_text=0.0,
                s_image=s_image,
                s_fusion=s_fusion,
            )

    return sorted(results.values(), key=lambda e: e.s_fusion, reverse=True)
