import logging
import asyncio
from asgiref.sync import sync_to_async
from madro.db import async_cursor
from madro.models import Agent, ExecutionStatus, JobExecution, JobStatus
import importlib
import json
import httpx
from madro.workflows.normalizer import MultimodalNormalizer
from madro.internal_agents.enrichment_agent import EnrichmentAgent
from madro.data_wrappers import RetrievalOut, NormalizedArtifact

logger = logging.getLogger(__name__)


def _load_local_agent(uri: str):
    module_path = uri.removeprefix("local://").removesuffix(".py").replace("/", ".")
    module = importlib.import_module(module_path)
    from madro.retrieval_agents.base import RetrievalAgent
    for attr in vars(module).values():
        if isinstance(attr, type) and issubclass(attr, RetrievalAgent) and attr is not RetrievalAgent:
            return attr()
    raise ValueError(f"No RetrievalAgent subclass found in {uri}")


class AgentRunner:
    def __init__(self, batch_limit: int = 10, interval: int = 3, timeout: int = 30):
        self.batch_limit = batch_limit
        self.interval = interval
        self.timeout = timeout
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
                    json.dumps(artifact.provenance),
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

    async def invoke(self, job: JobExecution) -> NormalizedArtifact:
        agent: Agent = job.agent
        payload = {
            "job_id": str(job.job_id),
            "demand": job.demand.content,
            "schema": agent.mcp_schema,
        }

        raw = None
        if agent.uri.startswith("local://"):
            local_agent = _load_local_agent(agent.uri)
            raw = await local_agent.run(**payload)
        else:
            # NOTE: note being used
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(agent.uri, json=payload)
                response.raise_for_status()
                raw = response.json()

        retrieval_out = RetrievalOut.model_validate(raw)
        retrieval_out.provenance = {"source": agent.uri, "agent": agent.name}

        if agent.modality == "image" and retrieval_out.image_content:
            await EnrichmentAgent().enrich_records(retrieval_out)

        multi_norm = MultimodalNormalizer()
        return await multi_norm.normalize(retrieval_out)

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
