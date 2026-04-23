from pydantic_ai import Agent
from madro.models import Agent as AgentModel, Topic, AgentTopic
from madro.workflows.models import TopicAssignment
from madro.workflows.system_prompts import TopicCategorizationSP
from madro.config import load_config


_cfg = load_config()

categorization_agent = Agent(
    model=f"openai:{_cfg.model.name}",
    output_type=TopicAssignment,
    system_prompt=TopicCategorizationSP,
)


@categorization_agent.tool_plain
def list_topics() -> list[dict]:
    return list(Topic.objects.values("id", "name", "description"))


async def categorize_and_assign(agent: AgentModel) -> list[Topic]:
    # prompt = (
    #     f"Agent name: {agent.name}\n"
    #     f"Description: {agent.description}\n"
    #     f"MCP schema: {agent.mcp_schema}\n"
    #     f"Candidate topics: {agent.candidate_topics or []}"
    # )
    prompt = agent.to_yaml()
    result = await categorization_agent.run(prompt)
    assignment: TopicAssignment = result.output

    topics: list[Topic] = []

    if assignment.new_topic:
        topic = Topic.objects.create(
            name=assignment.new_topic.name,
            description=assignment.new_topic.description,
        )
        topics.append(topic)
    else:
        topics = list(Topic.objects.filter(id__in=assignment.topic_ids))

    AgentTopic.objects.bulk_create(
        [AgentTopic(agent=agent, topic=t) for t in topics],
        ignore_conflicts=True,
    )

    return topics
