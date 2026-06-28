from pydantic_ai import Agent
from madro.data_wrappers import EnrichedPrompt
from madro.internal_agents.system_prompts import InteractiveAgentSP
from madro.config import get_model

interactive_agent = Agent(
    model=get_model(),
    output_type=EnrichedPrompt,
    system_prompt=InteractiveAgentSP,
)


async def enrich_prompt(task_prompt: str) -> str:
    result = await interactive_agent.run(task_prompt)
    return result.output.rewritten_prompt
