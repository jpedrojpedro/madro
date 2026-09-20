"""
Relevance Ranking.

Computes S_relevance(e) = alpha * S_lex(e) + beta * S_sem(e) for candidate
entities across all completed retrieval artifacts. All artifacts — text or
image-derived — are embedded into the same text vector space by
MultimodalNormalizer (OCR text / img_caption for images, raw text
otherwise), so entities are scored uniformly regardless of source modality.

Ranked per sub-demand, not per thread: each entity is scored only against
the text of the sub-demand whose JobExecution(s) actually produced it, and
each sub-demand's own top-k is unioned (not re-ranked globally) into the
final result — a numerically larger sub-demand's candidate pool can't crowd
another sub-demand's evidence out of the top-k. See
docs/adr/0009-relevance-ranking-is-scoped-per-sub-demand.md.

S_sem is computed per entity, live, against each entity's own resolved
record (MultimodalNormalizer.record_to_text) rather than read from
broker.job_artifact_document — those chunk embeddings are batch-level (one
retrieval agent call's whole result set synthesized into one document), so
every candidate in a batch inherited an identical S_sem, erasing semantic
differentiation between them. See
docs/adr/0004-per-entity-live-embedding-for-s-sem.md.

S_lex (ts_rank, typically ~0.01-0.03 here) and S_sem (cosine similarity,
typically ~0.5-0.65) live on very different numeric scales, so a raw
alpha*S_lex + beta*S_sem would let S_sem's magnitude dominate s_relevance
almost regardless of alpha/beta — the weights wouldn't actually control
influence the way their ratio suggests. Reciprocal Rank Fusion sidesteps
this: alpha/beta are applied to each entity's RANK within its own channel,
not the channel's raw value, so they stay meaningful regardless of either
channel's absolute scale.
"""

import math

from madro.config import load_config
from madro.db import async_cursor
from madro.workflows.normalizer import MultimodalNormalizer
from madro.aggregation.entities import RankedEntity
from madro.aggregation.entity_resolver import EntityResolver


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


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

    async def rank(self, thread_id: str) -> list[RankedEntity]:
        """Compute relevance scores for all candidate entities in a thread,
        scoring each against the sub-demand that actually asked for it —
        see docs/adr/0009-relevance-ranking-is-scoped-per-sub-demand.md."""
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
                       ja.provenance_details,
                       m.content
                FROM broker.job_artifact ja
                JOIN broker.job_status js ON js.id = ja.job_status_id
                JOIN broker.job_execution je ON je.job_id = js.job_id AND je.agent_id = js.agent_id
                JOIN agents_topics.agent a ON a.id = je.agent_id
                JOIN flow_control.message m ON m.id = je.demand_id
                WHERE je.thread_id = %s
                  AND js.status = 'completed'
                """,
                [thread_id],
            )
            artifact_rows = await cur.fetchall()

        # Group identity + records by artifact, and remember which sub-demand
        # (not the thread's top-level demand) actually asked for each one.
        records_by_artifact: dict[str, tuple[dict | None, list[dict]]] = {}
        sub_demand_by_artifact: dict[str, str] = {}
        for row in artifact_rows:
            artifact_id, provenance_details, sub_demand = row
            pd = provenance_details or {}
            records = pd.get("records") or []
            identity = pd.get("identity")
            records_by_artifact[str(artifact_id)] = (identity, records)
            sub_demand_by_artifact[str(artifact_id)] = sub_demand

        # Try to resolve entities by joining on common keys across artifacts
        entities = self._entity_resolver.resolve(records_by_artifact)

        if not entities:
            return []

        # S_sem per entity, live — see module docstring and ADR-0004 for why
        # this isn't read from broker.job_artifact_document. Computed once per
        # entity regardless of how many sub-demand pools it's scored in below —
        # the embedded text doesn't change, only the query it's compared to.
        entity_items = list(entities.items())
        entity_texts = [
            self._normalizer.record_to_text(entity.data)
            for _, entity in entity_items
        ]
        entity_embeddings = await self._normalizer._embed(entity_texts)
        embedding_by_entity = {
            entity_id: embedding
            for (entity_id, _), embedding in zip(entity_items, entity_embeddings)
        }

        # Each entity may belong to more than one sub-demand's pool (the same
        # real-world entity resolved from artifacts two different sub-demands
        # produced) — group by every sub-demand it belongs to, not just one.
        sub_demands_by_entity: dict[str, set[str]] = {}
        for entity_id, entity in entities.items():
            sub_demands_by_entity[entity_id] = {
                sub_demand_by_artifact[aid] for aid in entity.artifact_ids
            }
        pool_members: dict[str, list[str]] = {}
        for entity_id, sub_demands in sub_demands_by_entity.items():
            for sub_demand in sub_demands:
                pool_members.setdefault(sub_demand, []).append(entity_id)

        query_embedding_by_sub_demand = {
            sub_demand: self._embed_query(sub_demand) for sub_demand in pool_members
        }

        best: dict[str, RankedEntity] = {}
        for sub_demand, member_ids in pool_members.items():
            query_embedding = query_embedding_by_sub_demand[sub_demand]

            # Raw scores within this sub-demand's own candidate pool only.
            raw: list[tuple[str, dict, float, float]] = []
            for entity_id in member_ids:
                entity_data = entities[entity_id].data
                # Records with no `rnk` at all (e.g. SemanticOpinionFetcherAgent's
                # comments have no lexical query of their own) default to 0.0 —
                # they're only findable via the semantic channel today.
                s_lex = float(entity_data.get("rnk") or 0.0)
                s_sem = _cosine(query_embedding, embedding_by_entity[entity_id])
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

            pool_results: list[RankedEntity] = []
            for entity_id, entity_data, s_lex, s_sem in raw:
                rrf_lex = 1.0 / (self._RRF_K + lex_rank[entity_id])
                rrf_sem = 1.0 / (self._RRF_K + sem_rank[entity_id])
                s_relevance = self.alpha * rrf_lex + self.beta * rrf_sem
                pool_results.append(RankedEntity(
                    entity_id=entity_id,
                    entity_data=entity_data,
                    s_lex=s_lex,
                    s_sem=s_sem,
                    s_relevance=s_relevance,
                    sub_demands=[sub_demand],
                ))

            pool_results.sort(key=lambda e: e.s_relevance, reverse=True)
            # Union across sub-demands — deliberately not re-ranked globally,
            # so a numerically larger sub-demand's pool can't crowd another
            # sub-demand's own top-k out of the final evidence set.
            for entity in pool_results[:self.top_k]:
                existing = best.get(entity.entity_id)
                if existing is None:
                    best[entity.entity_id] = entity
                elif entity.s_relevance > existing.s_relevance:
                    entity.sub_demands = existing.sub_demands + entity.sub_demands
                    best[entity.entity_id] = entity
                else:
                    existing.sub_demands = existing.sub_demands + entity.sub_demands

        results = list(best.values())
        results.sort(key=lambda e: e.s_relevance, reverse=True)
        return results
