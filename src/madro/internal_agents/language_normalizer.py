from pydantic_ai import Agent
from madro.data_wrappers import TranslatedPrompt
from madro.internal_agents.system_prompts import LanguageNormalizationSP
from madro.config import get_model, run_agent

language_normalizer_agent = Agent(
    model=get_model(),
    output_type=TranslatedPrompt,
    system_prompt=LanguageNormalizationSP,
)


async def normalize_language(prompt: str) -> str:
    result = await run_agent(language_normalizer_agent, prompt)
    return result.output.translated_prompt
