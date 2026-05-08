from madro.models import AgentTopic, JobExecution, Message, Thread, Topic
from madro.data_wrappers import DecomposedDemand


async def publish(
    thread: Thread,
    demand_message: Message,
    decomposed: DecomposedDemand,
) -> list[JobExecution]:
    topic_names = [sd.topic_name for sd in decomposed.sub_demands]

    topics: dict[str, Topic] = {
        t.name: t
        async for t in Topic.objects.filter(name__in=topic_names)
    }

    jobs: list[JobExecution] = []

    for sub_demand in decomposed.sub_demands:
        topic = topics.get(sub_demand.topic_name)
        if not topic:
            continue

        async for agent_topic in AgentTopic.objects.filter(topic=topic, is_active=True).select_related("agent"):
            job = JobExecution(
                thread=thread,
                demand=demand_message,
                topic=topic,
                agent=agent_topic.agent,
            )
            await job.asave()
            jobs.append(job)

    return jobs
