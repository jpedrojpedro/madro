from pydantic_ai import Agent
from madro.models import Topic
from madro.data_wrappers import DecomposedDemand
from madro.workflows.system_prompts import DemandCategorizationAgentSP
from madro.config import load_config

_cfg = load_config()


async def decompose_demand(enriched_prompt: str) -> DecomposedDemand:
    topic_names = [t async for t in Topic.objects.values_list("name", flat=True)]

    agent = Agent(
        model=f"openai:{_cfg.model.name}",
        output_type=DecomposedDemand,
        system_prompt=DemandCategorizationAgentSP.format(topic_names=", ".join(topic_names)),
    )

    result = await agent.run(enriched_prompt)
    return result.output
