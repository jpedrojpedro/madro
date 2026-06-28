from uuid import UUID
from madro.models import Thread, Message, MessageRole
from madro.internal_agents.interactive_agent import enrich_prompt
from madro.internal_agents.demand_categorization_agent import decompose_demand
from madro.broker.publisher import publish
from madro.data_wrappers import DecomposedDemand


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

    enriched = await enrich_prompt(task_prompt)
    decomposed = await decompose_demand(enriched)

    await Message.objects.acreate(
        thread=thread,
        role=MessageRole.ASSISTANT,
        content=enriched,
        sequence_number=last_sequence + 2,
    )

    await publish(thread, user_message, decomposed)

    return thread, user_message, decomposed
