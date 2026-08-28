"""
Relevance Ranking.

Computes S_relevance(e) = alpha * S_lex(e) + beta * S_sem(e) for candidate
entities across all completed retrieval artifacts. All artifacts — text or
image-derived — are embedded into the same text vector space by
MultimodalNormalizer (OCR text / img_caption for images, raw text
otherwise), so entities are scored uniformly regardless of source modality.

S_lex (ts_rank, typically ~0.01-0.03 here) and S_sem (cosine similarity,
typically ~0.5-0.65) live on very different numeric scales, so a raw
alpha*S_lex + beta*S_sem would let S_sem's magnitude dominate s_relevance
almost regardless of alpha/beta — the weights wouldn't actually control
influence the way their ratio suggests. Reciprocal Rank Fusion sidesteps
this: alpha/beta are applied to each entity's RANK within its own channel,
not the channel's raw value, so they stay meaningful regardless of either
channel's absolute scale.
"""

from madro.config import load_config
from madro.db import async_cursor
from madro.workflows.normalizer import MultimodalNormalizer
from madro.aggregation.entities import RankedEntity
from madro.aggregation.entity_resolver import EntityResolver


class RelevanceRanker:
    # Standard Reciprocal Rank Fusion damping constant (Cormack et al. 2009) —
    # large enough that a rank-1 vs rank-2 difference doesn't swing the fused
    # score wildly, without needing to be tuned per corpus.
    _RRF_K = 60

    def __init__(
        self,
        alpha: float | None = None,
        beta: float | None = None,
        top_k: int = 10,
        normalizer: MultimodalNormalizer | None = None,
        entity_resolver: EntityResolver | None = None,
    ):
        cfg = load_config()
        self.alpha = alpha if alpha is not None else cfg.fusion.alpha
        self.beta = beta if beta is not None else cfg.fusion.beta
        self.top_k = top_k
        self._normalizer = normalizer or MultimodalNormalizer()
        self._entity_resolver = entity_resolver or EntityResolver()

    def _embed_query(self, query: str) -> list[float]:
        return self._normalizer._get_encoder().encode(query).tolist()

    async def rank(self, thread_id: str, demand: str) -> list[RankedEntity]:
        """Compute relevance scores for all candidate entities in a thread."""
        query_embedding = self._embed_query(demand)
        vector_literal = "[" + ",".join(map(str, query_embedding)) + "]"

        async with async_cursor() as cur:
            # Per-artifact identity + raw records, for entity resolution. s_lex
            # is NOT recomputed here — each retrieval agent already computes its
            # own ts_rank at fetch time (against dowser's correctly-configured
            # `pt_en` lexeme columns, scoped to that agent's own sub-demand), and
            # that `rnk` is reused as-is below via entity_data. Recomputing it
            # centrally against MADRO's own re-derived lexical_vector duplicated
            # that work against a different (and until recently, misconfigured)
            # text representation and a different query text (the thread's
            # top-level demand rather than each artifact's own sub-demand).
            #
            # Known gap: image-modality artifacts have no lexical score of their
            # own for the img_caption/ocr_text EnrichmentAgent adds after
            # retrieval — ImageFetcherAgent's `rnk` only covers the linked
            # publication's caption. A future enhancement could score that
            # enriched text here (e.g. ts_rank against `pt_en` on canonical_text)
            # instead of relying solely on the semantic channel for it.
            await cur.execute(
                """
                SELECT ja.job_status_id,
                       ja.provenance_details
                FROM broker.job_artifact ja
                JOIN broker.job_status js ON js.id = ja.job_status_id
                JOIN broker.job_execution je ON je.job_id = js.job_id AND je.agent_id = js.agent_id
                JOIN agents_topics.agent a ON a.id = je.agent_id
                WHERE je.thread_id = %s
                  AND js.status = 'completed'
                """,
                [thread_id],
            )
            artifact_rows = await cur.fetchall()

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

        # Group identity + records by artifact
        records_by_artifact: dict[str, tuple[dict | None, list[dict]]] = {}
        for row in artifact_rows:
            artifact_id, provenance_details = row
            pd = provenance_details or {}
            records = pd.get("records") or []
            identity = pd.get("identity")
            records_by_artifact[str(artifact_id)] = (identity, records)

        # Group semantic scores by artifact — best chunk score per artifact
        sem_by_artifact: dict[str, float] = {}
        for row in sem_rows:
            artifact_id, sem_score = row
            key = str(artifact_id)
            sem_by_artifact[key] = max(sem_by_artifact.get(key, 0.0), float(sem_score))

        # Try to resolve entities by joining on common keys across artifacts
        entities = self._entity_resolver.resolve(records_by_artifact)

        # Raw scores first (kept on RankedEntity for observability/debugging —
        # they're what actually surfaced the scale-mismatch problem this RRF
        # step fixes). s_relevance is computed from each entity's RANK within
        # a channel, not these raw values — see module docstring.
        raw: list[tuple[str, dict, float, float]] = []
        for entity_id, (artifact_ids, entity_data) in entities.items():
            # Records with no `rnk` at all (e.g. SemanticOpinionFetcherAgent's
            # comments have no lexical query of their own) default to 0.0 —
            # they're only findable via the semantic channel today.
            s_lex = float(entity_data.get("rnk") or 0.0)
            s_sem = max(sem_by_artifact.get(aid, 0.0) for aid in artifact_ids)
            raw.append((entity_id, entity_data, s_lex, s_sem))

        lex_rank = {
            entity_id: rank
            for rank, (entity_id, *_) in enumerate(
                sorted(raw, key=lambda r: r[2], reverse=True), start=1
            )
        }
        sem_rank = {
            entity_id: rank
            for rank, (entity_id, *_) in enumerate(
                sorted(raw, key=lambda r: r[3], reverse=True), start=1
            )
        }

        results: list[RankedEntity] = []
        for entity_id, entity_data, s_lex, s_sem in raw:
            rrf_lex = 1.0 / (self._RRF_K + lex_rank[entity_id])
            rrf_sem = 1.0 / (self._RRF_K + sem_rank[entity_id])
            s_relevance = self.alpha * rrf_lex + self.beta * rrf_sem
            results.append(RankedEntity(
                entity_id=entity_id,
                entity_data=entity_data,
                s_lex=s_lex,
                s_sem=s_sem,
                s_relevance=s_relevance,
            ))

        results.sort(key=lambda e: e.s_relevance, reverse=True)
        return results[:self.top_k]
