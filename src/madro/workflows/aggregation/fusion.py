"""
Cross-modality Fusion.

Computes S_fusion(e) = w_t · S_t(e) + w_i · S_i(e)
Currently only text modality is implemented.
"""

from dataclasses import dataclass

from madro.config import load_config
from madro.workflows.aggregation.text_agent import ScoredEntity, aggregate_text


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

    text_scores = await aggregate_text(thread_id, demand)
    # image_scores: placeholder for future Aggregation Image-to-Text Agent
    image_scores: dict[str, float] = {}

    results: list[FusedEntity] = []
    for entity in text_scores:
        s_image = image_scores.get(entity.entity_id, 0.0)
        s_fusion = w_t * entity.s_text + w_i * s_image
        results.append(FusedEntity(
            entity_id=entity.entity_id,
            entity_data=entity.entity_data,
            s_text=entity.s_text,
            s_image=s_image,
            s_fusion=s_fusion,
        ))

    results.sort(key=lambda e: e.s_fusion, reverse=True)
    return results
