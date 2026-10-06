from pydantic_ai import Agent
from madro.models import AgentTopic, Topic
from madro.data_wrappers import DecomposedDemand
from madro.internal_agents.system_prompts import DemandCategorizationAgentSP
from madro.config import get_model, run_agent


async def _build_topic_catalog() -> tuple[str, set[str]]:
    """One line per topic: its name, its description (authored by
    TopicCategorizationAgent when the topic was created — see
    topic_categorization_agent.py), and the entity kind(s) its active agents
    actually return (from Agent.identity) — so the categorization agent can
    judge a sub-demand's retrieval target against what a topic really
    resolves to, instead of guessing from the topic name alone. Also returns
    every distinct entity kind seen across the catalog, as the controlled
    vocabulary target_entity must be chosen from."""
    topics = [t async for t in Topic.objects.all().order_by("name")]

    kinds_by_topic: dict = {}
    all_kinds: set[str] = set()
    async for agent_topic in AgentTopic.objects.filter(is_active=True).select_related("agent", "topic"):
        if agent_topic.agent.identity:
            kind = agent_topic.agent.identity["kind"]
            kinds_by_topic.setdefault(agent_topic.topic_id, set()).add(kind)
            all_kinds.add(kind)

    lines = []
    for topic in topics:
        kinds = kinds_by_topic.get(topic.id)
        returns = ", ".join(sorted(kinds)) if kinds else "no specific entity"
        lines.append(f"- {topic.name}: {topic.description} (returns: {returns})")

    return "\n".join(lines), all_kinds


async def decompose_demand(enriched_prompt: str) -> DecomposedDemand:
    topics, entity_kinds = await _build_topic_catalog()

    agent = Agent(
        model=get_model(),
        output_type=DecomposedDemand,
        system_prompt=DemandCategorizationAgentSP.format(
            topics=topics, entity_kinds=", ".join(sorted(entity_kinds))
        ),
    )

    result = await run_agent(agent, enriched_prompt)
    return result.output
