import base64
import asyncio
import importlib
import json
from functools import partial
import httpx
from madro.models import Agent, JobExecution
from madro.workflows.normalizer import NormalisedArtifact, normalise
from madro.workflows.aggregation.image_agent import describe_images
from madro.data_wrappers import RetrievalOut


def _load_local_agent(uri: str):
    module_path = uri.removeprefix("local://").removesuffix(".py").replace("/", ".")
    module = importlib.import_module(module_path)
    from madro.retrieval_agents.base import RetrievalAgent
    for attr in vars(module).values():
        if isinstance(attr, type) and issubclass(attr, RetrievalAgent) and attr is not RetrievalAgent:
            return attr()
    raise ValueError(f"No RetrievalAgent subclass found in {uri}")


def _handle_response(resp) -> RetrievalOut | None:
    def is_base64(s: str) -> bool:
        try:
            if isinstance(s, str):
                s_bytes = s.encode('utf-8')
            else:
                s_bytes = s
            base64.b64decode(s_bytes, validate=True)
            return True
        except:
            return False

    resp_type = type(resp)
    if resp_type not in (str, dict, list):
        return None

    if resp_type == str:
        ro = RetrievalOut()
        if is_base64(resp):
            ro.image_content = [resp]
        else:
            ro.text_content = [resp]
        return ro

    if resp_type == dict:
        ro = RetrievalOut()
        ro.text_content = []
        for key, val in resp.items():
            if isinstance(val, str) and is_base64(val):
                ro.image_content = [val]
            else:
                ro.text_content.append({key: val})
        return ro

    if resp_type == list:
        first_item = resp[0]
        if not isinstance(first_item, dict):
            return None

        ro = RetrievalOut()
        ro.text_content = []
        ro.image_content = []
        for dict_elem in resp:
            img_content, txt_content = None, {}
            for key, val in dict_elem.items():
                if isinstance(val, str) and is_base64(val):
                    img_content = val
                else:
                    txt_content[key] = val
            ro.text_content.append(txt_content)
            ro.image_content.append(img_content)

        return ro

    return None


async def invoke(job: JobExecution) -> NormalisedArtifact:
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
        # TODO: not being used
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(agent.uri, json=payload)
            response.raise_for_status()
            raw = response.json()

    retrieval_out = _handle_response(raw)
    retrieval_out.provenance = {"source": agent.uri, "agent": agent.name}

    if agent.modality == "image" and retrieval_out.image_content:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(describe_images, retrieval_out)
        )

    return await normalise(retrieval_out)
