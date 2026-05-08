import json
import logging
from asgiref.sync import sync_to_async
from django.db import connection
from madro.models import ExecutionStatus, JobExecution, JobStatus
from madro.workflows.normalizer import NormalisedArtifact
from madro.workflows.retrieval_agent import invoke

logger = logging.getLogger(__name__)


async def _persist_artifact(job_status: JobStatus, artifact: NormalisedArtifact, mean_vector: list[float]) -> None:
    vector_literal = "[" + ",".join(map(str, mean_vector)) + "]"

    def _insert():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO broker.job_artifact
                    (job_status_id, canonical_text, provenance_details, lexical_vector, semantic_embedding)
                VALUES (%s, %s, %s::jsonb, to_tsvector('english', %s), %s::vector)
                """,
                [
                    str(job_status.id),
                    artifact.canonical_text,
                    json.dumps(artifact.provenance),
                    artifact.lexical_index.normalization,
                    vector_literal,
                ],
            )

    await sync_to_async(_insert)()


def _mean_embedding(embeddings: list[list[float]]) -> list[float]:
    n = len(embeddings)
    return [sum(col) / n for col in zip(*embeddings)]


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
            mean_vector = _mean_embedding(artifact.semantic_index.embeddings)
            await _persist_artifact(job_status, artifact, mean_vector)

            job_status.status = ExecutionStatus.COMPLETED
            await job_status.asave(update_fields=["status"])

        except Exception:
            logger.exception("AgentRunner failed for job_id=%s agent=%s", job.job_id, job.agent_id)
            job_status.status = ExecutionStatus.FAILED
            await job_status.asave(update_fields=["status"])
