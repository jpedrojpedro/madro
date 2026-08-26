import logging
import re
import asyncio
from asgiref.sync import sync_to_async
from madro.db import async_cursor
from madro.models import Agent, ExecutionStatus, JobExecution, JobStatus
import json
from madro.workflows.normalizer import MultimodalNormalizer
from madro.internal_agents.enrichment_agent import EnrichmentAgent
from madro.data_wrappers import RetrievalOut, NormalizedArtifact
from madro.retrieval_agents.base import RetrievalAgent

logger = logging.getLogger(__name__)


class AgentRunner:
    def __init__(
            self,
            batch_limit: int = 10,
            interval: int = 3,
            timeout: int = 30,
            enrichment_agent: EnrichmentAgent | None = None,
            normalizer: MultimodalNormalizer | None = None,
    ):
        self.batch_limit = batch_limit
        self.interval = interval
        self.timeout = timeout
        # Shared across invoke() calls — EnrichmentAgent/MultimodalNormalizer lazily
        # load their models on first use, so a fresh instance per job would reload
        # the VLM (and encoder) from scratch every time, growing memory unboundedly
        # over a long-running batch.
        self._enrichment_agent = enrichment_agent or EnrichmentAgent()
        self._normalizer = normalizer or MultimodalNormalizer()
        self.queue_query = """
           WITH oldest_incomplete_job AS (
               SELECT DISTINCT je.job_id, je.created_at
               FROM broker.job_execution je
               LEFT JOIN broker.job_status js ON je.job_id = js.job_id
               WHERE js.job_id IS NULL
               ORDER BY je.created_at
               LIMIT %s
           )
           SELECT je.*,
                  a.id      AS agent__id,
                  m.id      AS demand__id,
                  m.content AS demand__content
           FROM broker.job_execution je
           JOIN oldest_incomplete_job oij ON je.job_id = oij.job_id
           JOIN agents_topics.agent a ON je.agent_id = a.id
           JOIN flow_control.message m ON je.demand_id = m.id
        """

    async def fetch_next_batch(self) -> list[JobExecution]:
        def execute():
            return list(
                JobExecution.objects.raw(self.queue_query, [self.batch_limit])
            )

        return await sync_to_async(execute, thread_sensitive=False)()

    async def persist_artifact(
        self, job_status: JobStatus, artifact: NormalizedArtifact
    ) -> None:
        async with async_cursor() as cur:
            await cur.execute(
                """
                INSERT INTO broker.job_artifact
                (job_status_id, canonical_text, provenance_details, lexical_vector)
                VALUES (%s, %s, %s::jsonb, to_tsvector('english', %s))
                """,
                [
                    str(job_status.id),
                    artifact.canonical_text,
                    json.dumps({**artifact.provenance, "records": artifact.raw_records}),
                    artifact.lexical_index.normalization,
                ],
            )

            chunks = artifact.semantic_index.chunks
            embeddings = artifact.semantic_index.embeddings
            for idx, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                vector_literal = "[" + ",".join(map(str, emb)) + "]"
                await cur.execute(
                    """
                    INSERT INTO broker.job_artifact_document
                    (job_artifact_id, chunk_index, chunk_text, embedding)
                    VALUES (%s, %s, %s, %s::vector)
                    """,
                    [str(job_status.id), idx, chunk, vector_literal],
                )

    _MENTION_RE = re.compile(r"@(\w+(?:\.\w+)*)")

    @classmethod
    def _extract_mentioned_username(cls, demand: str) -> tuple[str, str | None]:
        """Splits an explicit `@username` profile mention out of the demand text
        (e.g. "perception of @haight_clothing" -> ("perception of", "haight_clothing")),
        so retrieval agents can filter by an exact profile match instead of relying
        on lexical/text search to surface the handle by coincidence."""
        match = cls._MENTION_RE.search(demand)
        if not match:
            return demand, None
        cleaned = (demand[:match.start()] + demand[match.end():]).strip()
        return cleaned, match.group(1)

    async def invoke(self, job: JobExecution, sample: int = 10) -> NormalizedArtifact:
        agent: Agent = job.agent
        demand, username = self._extract_mentioned_username(job.demand.content)
        payload = {
            "job_id": str(job.job_id),
            "demand": demand,
            "schema": agent.mcp_schema,
            "sample": sample,
            "username": username,
        }

        retrieval_agent = RetrievalAgent.from_uri(agent.uri, timeout=self.timeout)
        raw = await retrieval_agent.run(**payload)

        retrieval_out = RetrievalOut.model_validate(raw)
        retrieval_out.provenance = {
            "source": agent.uri,
            "agent": agent.name,
            "identity": agent.identity,
        }

        if agent.modality == "image" and retrieval_out.image_content:
            await self._enrichment_agent.enrich_records(retrieval_out)

        return await self._normalizer.normalize(retrieval_out)

    async def process_single_job(self, job: JobExecution) -> None:
        # TODO: handle racing-condition
        job_status = await JobStatus.objects.acreate(
            job=job,
            agent=job.agent,
            status=ExecutionStatus.PROCESSING,
        )

        try:
            # Explicit call to instance context 'self'
            artifact = await self.invoke(job)
            await self.persist_artifact(job_status, artifact)
            job_status.status = ExecutionStatus.COMPLETED
        except Exception:
            logger.exception(
                "AgentRunner failed for job_id=%s agent=%s",
                job.job_id,
                job.agent_id
            )
            job_status.status = ExecutionStatus.FAILED
        finally:
            await job_status.asave(update_fields=["status"])

    async def start_polling(self) -> None:
        while True:
            try:
                pending_jobs = await self.fetch_next_batch()
                if not pending_jobs:
                    await asyncio.sleep(self.interval)
                    continue

                await asyncio.gather(
                    *(self.process_single_job(job) for job in pending_jobs))
            except Exception as e:
                logger.error("Consumer loop encountered an error: %s", e)
                await asyncio.sleep(self.interval)
