import logging
from django.db import connection
from openai import AsyncOpenAI
from madro.models import Agent, ExecutionStatus, JobArtifact, JobExecution, JobStatus
from madro.workflows.retrieval_agent import invoke

logger = logging.getLogger(__name__)


async def _persist_artifact(job_status: JobStatus, artifact, embedding: list[float]) -> None:
    # lexical_vector requires to_tsvector(); semantic_embedding is the mean vector of all chunks
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO broker.job_artifact
                (job_status_id, canonical_text, provenance_details, lexical_vector, semantic_embedding)
            VALUES (%s, %s, %s, to_tsvector('english', %s), %s::vector)
            """,
            [
                str(job_status.id),
                artifact.canonical_text,
                artifact.provenance,
                artifact.lexical_text,
                str(embedding),
            ],
        )


def _mean_embedding(embeddings: list[list[float]]) -> list[float]:
    n = len(embeddings)
    return [sum(col) / n for col in zip(*embeddings)]


async def run(openai_client: AsyncOpenAI | None = None) -> None:
    if openai_client is None:
        openai_client = AsyncOpenAI()

    # fetch jobs with no status entry yet
    pending_jobs = [
        job async for job in JobExecution.objects.select_related("agent", "demand")
        if not await JobStatus.objects.filter(job_id=job.job_id, agent=job.agent).aexists()
    ]

    for job in pending_jobs:
        job_status = await JobStatus.objects.acreate(
            job_id=job.job_id,
            agent=job.agent,
            status=ExecutionStatus.PROCESSING,
        )
        try:
            artifact = await invoke(job, openai_client)
            embedding = _mean_embedding(artifact.embeddings)
            await _persist_artifact(job_status, artifact, embedding)

            job_status.status = ExecutionStatus.COMPLETED
            await job_status.asave(update_fields=["status"])

        except Exception:
            logger.exception("AgentRunner failed for job_id=%s agent=%s", job.job_id, job.agent_id)
            job_status.status = ExecutionStatus.FAILED
            await job_status.asave(update_fields=["status"])
