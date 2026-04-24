import importlib
import httpx
from openai import AsyncOpenAI
from madro.models import Agent, JobExecution
from madro.workflows.normalizer import NormalisedArtifact, normalise


def _load_local_agent(uri: str):
    """Convert local://madro/retrieval_agents/foo.py to a module, instantiate its RetrievalAgent subclass."""
    # strip scheme → madro/retrieval_agents/foo.py
    module_path = uri.removeprefix("local://").removesuffix(".py").replace("/", ".")
    module = importlib.import_module(module_path)
    from madro.retrieval_agents.base import RetrievalAgent
    for attr in vars(module).values():
        if isinstance(attr, type) and issubclass(attr, RetrievalAgent) and attr is not RetrievalAgent:
            return attr()
    raise ValueError(f"No RetrievalAgent subclass found in {uri}")


async def invoke(job: JobExecution, openai_client: AsyncOpenAI) -> NormalisedArtifact:
    agent: Agent = job.agent
    payload = {
        "job_id": str(job.job_id),
        "demand": job.demand.content,
        "schema": agent.mcp_schema,
    }

    if agent.uri.startswith("local://"):
        local_agent = _load_local_agent(agent.uri)
        raw = await local_agent.run(**payload)
    else:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(agent.uri, json=payload)
            response.raise_for_status()
            raw = response.json().get("result", response.text)

    return await normalise(
        raw=raw,
        source_uri=agent.uri,
        agent_name=agent.name,
        openai_client=openai_client,
    )
