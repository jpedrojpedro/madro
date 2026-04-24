import httpx
from openai import AsyncOpenAI
from madro.models import Agent, JobExecution
from madro.workflows.normalizer import NormalisedArtifact, normalise


async def invoke(job: JobExecution, openai_client: AsyncOpenAI) -> NormalisedArtifact:
    agent: Agent = job.agent

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            agent.uri,
            json={
                "job_id": str(job.job_id),
                "demand": job.demand.content,
                "schema": agent.mcp_schema,
            },
        )
        response.raise_for_status()
        raw: str = response.json().get("result", response.text)

    return await normalise(
        raw=raw,
        source_uri=agent.uri,
        agent_name=agent.name,
        openai_client=openai_client,
    )
