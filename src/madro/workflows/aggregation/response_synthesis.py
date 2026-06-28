"""
Response Synthesis Agent.

Takes the ranked fusion output and produces a natural-language answer
for the end user via LLM.
"""

import json

from pydantic_ai import Agent

from madro.config import get_model
from madro.workflows.aggregation.fusion import fuse
from madro.internal_agents.system_prompts import ResponseSynthesisSP


async def synthesize(thread_id: str, demand: str) -> str:
    """Run fusion and synthesize a natural-language response."""
    ranked = await fuse(thread_id, demand)
    evidence = json.dumps(
        [
            {"entity_id": e.entity_id, "score": round(e.s_fusion, 4), **e.entity_data}
            for e in ranked
        ],
        ensure_ascii=False,
    )

    agent = Agent(
        model=get_model(),
        system_prompt=ResponseSynthesisSP.format(demand=demand, evidence=evidence),
    )

    result = await agent.run(demand)
    return result.output
