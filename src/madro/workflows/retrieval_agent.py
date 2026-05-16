import importlib
import json
import httpx
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


def _strip_binary_fields(raw: str) -> str:
    """Remove bytea fields from JSON rows before normalization to avoid corrupting embeddings."""
    try:
        records = json.loads(raw)
        if isinstance(records, list):
            return json.dumps([{k: v for k, v in r.items() if not isinstance(v, (bytes, memoryview))} for r in records])
    except (json.JSONDecodeError, TypeError):
        pass
    return raw


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
        provenance = {
            "source": agent.uri,
            "agent": agent.name,
        }
    else:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(agent.uri, json=payload)
            response.raise_for_status()
            response_data = response.json()
        raw = response_data["result"]
        provenance = response_data["provenance"]

    if agent.modality == "image":
        raw = _strip_binary_fields(raw)

    return await normalise(raw=raw, provenance=provenance)
