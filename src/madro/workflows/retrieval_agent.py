import asyncio
import importlib
import json
from functools import partial

import httpx
from madro.models import Agent, JobExecution
from madro.workflows.normalizer import NormalisedArtifact, normalise
from madro.workflows.aggregation.image_agent import describe_images


def _load_local_agent(uri: str):
    module_path = uri.removeprefix("local://").removesuffix(".py").replace("/", ".")
    module = importlib.import_module(module_path)
    from madro.retrieval_agents.base import RetrievalAgent
    for attr in vars(module).values():
        if isinstance(attr, type) and issubclass(attr, RetrievalAgent) and attr is not RetrievalAgent:
            return attr()
    raise ValueError(f"No RetrievalAgent subclass found in {uri}")


async def invoke(job: JobExecution) -> NormalisedArtifact:
    agent: Agent = job.agent
    payload = {
        "job_id": str(job.job_id),
        "demand": job.demand.content,
        "schema": agent.mcp_schema,
    }

    if agent.uri.startswith("local://"):
        local_agent = _load_local_agent(agent.uri)
        raw = await local_agent.run(**payload)
        provenance = {"source": agent.uri, "agent": agent.name}
    else:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(agent.uri, json=payload)
            response.raise_for_status()
            response_data = response.json()
        raw = response_data["result"]
        provenance = response_data["provenance"]

    if agent.modality == "image":
        records = json.loads(raw)
        loop = asyncio.get_event_loop()
        enriched = await loop.run_in_executor(None, partial(describe_images, records))
        raw = json.dumps(enriched)

    return await normalise(raw=raw, provenance=provenance)
