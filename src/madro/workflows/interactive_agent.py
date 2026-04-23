from pydantic_ai import Agent
from madro.workflows.models import EnrichedPrompt
from madro.workflows.system_prompts import InteractiveAgentSP
from madro.config import load_config

_cfg = load_config()

interactive_agent = Agent(
    model=f"openai:{_cfg.model.name}",
    output_type=EnrichedPrompt,
    system_prompt=InteractiveAgentSP,
)


async def enrich_prompt(task_prompt: str) -> str:
    result = await interactive_agent.run(task_prompt)
    return result.output.rewritten_prompt
