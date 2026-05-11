import json
import logging
from madro.db import async_cursor
from madro.models import ExecutionStatus, JobExecution, JobStatus
from madro.workflows.normalizer import NormalisedArtifact
from madro.workflows.retrieval_agent import invoke

logger = logging.getLogger(__name__)


async def _persist_artifact(job_status: JobStatus, artifact: NormalisedArtifact) -> None:
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


async def run() -> None:
    # fetch jobs with no status entry yet
    pending_jobs = [
        job async for job in JobExecution.objects.select_related("agent", "demand")
        if not await JobStatus.objects.filter(job=job, agent=job.agent).aexists()
    ]

    for job in pending_jobs:
        job_status = await JobStatus.objects.acreate(
            job=job,
            agent=job.agent,
            status=ExecutionStatus.PROCESSING,
        )
        try:
            artifact = await invoke(job)
            await _persist_artifact(job_status, artifact)

            job_status.status = ExecutionStatus.COMPLETED
            await job_status.asave(update_fields=["status"])

        except Exception:
            logger.exception("AgentRunner failed for job_id=%s agent=%s", job.job_id, job.agent_id)
            job_status.status = ExecutionStatus.FAILED
            await job_status.asave(update_fields=["status"])
