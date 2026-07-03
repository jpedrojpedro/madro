"""
Relevance Ranking.

Computes S_relevance(e) = alpha * S_lex(e) + beta * S_sem(e) for candidate
entities across all completed retrieval artifacts. All artifacts — text or
image-derived — are embedded into the same text vector space by
MultimodalNormalizer (OCR text / img_caption for images, raw text
otherwise), so entities are scored uniformly regardless of source modality.
"""

from madro.config import load_config
from madro.db import async_cursor
from madro.workflows.normalizer import MultimodalNormalizer
from madro.aggregation.entities import RankedEntity
from madro.aggregation.entity_resolver import EntityResolver


class RelevanceRanker:
    def __init__(
        self,
        alpha: float | None = None,
        beta: float | None = None,
        normalizer: MultimodalNormalizer | None = None,
        entity_resolver: EntityResolver | None = None,
    ):
        cfg = load_config()
        self.alpha = alpha if alpha is not None else cfg.fusion.alpha
        self.beta = beta if beta is not None else cfg.fusion.beta
        self._normalizer = normalizer or MultimodalNormalizer()
        self._entity_resolver = entity_resolver or EntityResolver()

    def _embed_query(self, query: str) -> list[float]:
        return self._normalizer._get_encoder().encode(query).tolist()

    async def rank(self, thread_id: str, demand: str) -> list[RankedEntity]:
        """Compute relevance scores for all candidate entities in a thread."""
        query_embedding = self._embed_query(demand)
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
                JOIN agents_topics.agent a ON a.id = je.agent_id
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
                JOIN agents_topics.agent a ON a.id = je.agent_id
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
        entities = self._entity_resolver.resolve(lex_by_artifact)

        results: list[RankedEntity] = []
        for entity_id, (artifact_ids, entity_data) in entities.items():
            s_lex = max(lex_by_artifact[aid][0] for aid in artifact_ids if aid in lex_by_artifact)
            s_sem = max(sem_by_artifact.get(aid, 0.0) for aid in artifact_ids)
            s_relevance = self.alpha * s_lex + self.beta * s_sem
            results.append(RankedEntity(
                entity_id=entity_id,
                entity_data=entity_data,
                s_lex=s_lex,
                s_sem=s_sem,
                s_relevance=s_relevance,
            ))

        results.sort(key=lambda e: e.s_relevance, reverse=True)
        return results
