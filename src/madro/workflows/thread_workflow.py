from uuid import UUID
from madro.models import Thread, Message, MessageRole
from madro.internal_agents.interactive_agent import enrich_prompt
from madro.internal_agents.language_normalizer import normalize_language
from madro.internal_agents.demand_categorization_agent import decompose_demand
from madro.broker.publisher import publish
from madro.data_wrappers import DecomposedDemand
from madro.pseudonymization import extract_mentions, pseudonymize, depseudonymize


async def run_thread(thread_id: UUID | None, task_prompt: str) -> tuple[Thread, Message, DecomposedDemand]:
    if thread_id:
        thread = await Thread.objects.aget(id=thread_id)
    else:
        thread = await Thread.objects.acreate()

    last_sequence = await Message.objects.filter(thread=thread).acount()

    user_message = await Message.objects.acreate(
        thread=thread,
        role=MessageRole.USER,
        content=task_prompt,
        sequence_number=last_sequence + 1,
    )

    # Every @-mentioned handle is pseudonymized before it reaches any LLM
    # call from here on (enrichment, normalization, decomposition, and —
    # since sub_demand.demand carries the alias forward — every retrieval
    # agent's SQL-generation prompt too) — see pseudonymization.py. The
    # stored user_message above keeps the real text; only what flows onward
    # is aliased.
    handles = extract_mentions(task_prompt)
    enriched = await enrich_prompt(pseudonymize(task_prompt, handles))
    # Stands in for part of what a real, not-yet-implemented Clarification
    # Agent would do — dowser's corpus is majority Portuguese, so decomposition
    # and every retrieval agent's lexical search need the demand in that
    # language too; only the persisted, user-facing message below keeps the
    # enriched prompt in its original language.
    localized = await normalize_language(enriched)
    decomposed = await decompose_demand(localized)

    await Message.objects.acreate(
        thread=thread,
        role=MessageRole.ASSISTANT,
        content=depseudonymize(enriched, handles),
        sequence_number=last_sequence + 2,
    )

    await publish(thread, decomposed)

    return thread, user_message, decomposed
