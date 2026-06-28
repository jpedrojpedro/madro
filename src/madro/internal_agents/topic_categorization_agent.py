from pydantic_ai import Agent
from madro.models import Agent as AgentModel, Topic, AgentTopic
from madro.data_wrappers import TopicAssignment
from madro.internal_agents.system_prompts import TopicCategorizationSP
from madro.config import get_model


categorization_agent = Agent(
    model=get_model(),
    output_type=TopicAssignment,
    system_prompt=TopicCategorizationSP,
)


@categorization_agent.tool_plain
def list_topics() -> list[dict]:
    return list(Topic.objects.values("id", "name", "description"))


async def categorize_and_assign(agent: AgentModel) -> list[Topic]:
    prompt = agent.to_yaml()
    result = await categorization_agent.run(prompt)
    assignment: TopicAssignment = result.output

    topics: list[Topic] = []

    if assignment.new_topic:
        topic = Topic(
            name=assignment.new_topic.name,
            description=assignment.new_topic.description,
        )
        await topic.asave()
        topics.append(topic)
    else:
        topics = [t async for t in Topic.objects.filter(id__in=assignment.topic_ids)]

    await AgentTopic.objects.abulk_create(
        [AgentTopic(agent=agent, topic=t) for t in topics],
        ignore_conflicts=True,
    )

    return topics
