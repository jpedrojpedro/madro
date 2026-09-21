from madro.models import AgentTopic, JobExecution, Message, MessageRole, Thread, Topic
from madro.data_wrappers import DecomposedDemand


async def publish(
    thread: Thread,
    decomposed: DecomposedDemand,
) -> list[JobExecution]:
    topic_names = [sd.topic_name for sd in decomposed.sub_demands]

    topics: dict[str, Topic] = {
        t.name: t
        async for t in Topic.objects.filter(name__in=topic_names)
    }

    jobs: list[JobExecution] = []
    sequence_number = await Message.objects.filter(thread=thread).acount()

    for sub_demand in decomposed.sub_demands:
        topic = topics.get(sub_demand.topic_name)
        if not topic:
            continue

        # Each sub-demand gets its own Message row (rather than reusing
        # demand_message, the raw pre-decomposition prompt) so every
        # JobExecution/retrieval agent actually sees its own focused,
        # localized sub-demand text — not the undecomposed original.
        sequence_number += 1
        sub_demand_message = await Message.objects.acreate(
            thread=thread,
            role=MessageRole.SYSTEM,
            content=sub_demand.demand,
            sequence_number=sequence_number,
            target_entity=sub_demand.target_entity,
        )

        async for agent_topic in AgentTopic.objects.filter(topic=topic, is_active=True).select_related("agent"):
            job = JobExecution(
                thread=thread,
                demand=sub_demand_message,
                topic=topic,
                agent=agent_topic.agent,
            )
            await job.asave()
            jobs.append(job)

    return jobs
