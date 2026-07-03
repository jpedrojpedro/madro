"""Idempotent bootstrap for the RetrievalAgent catalog used by scripts/smoke_test.py
and the Allure benchmark suite (tests/benchmark/)."""

from madro.models import Agent, AgentTopic
from madro.internal_agents.topic_categorization_agent import categorize_and_assign

SEED_AGENTS: list[dict] = [
    {
        "name": "ProfileFetcherAgent",
        "description": (
            "Fetches public profile data from Instagram business accounts, "
            "including name, bio, category, location, address, and contact details."
        ),
        "uri": "local://madro/retrieval_agents/profile_fetcher_agent",
        "mcp_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Instagram account handle"},
            },
            "required": ["account"],
        },
        "candidate_topics": ["profile_retrieval", "location_retrieval"],
    },
    {
        "name": "PublicationFetcherAgent",
        "description": (
            "Fetches recent posts and publications from Instagram business accounts, "
            "including captions, hashtags, media type, and engagement metrics."
        ),
        "uri": "local://madro/retrieval_agents/publication_fetcher_agent",
        "mcp_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Instagram account handle"},
                "limit": {"type": "integer", "description": "Max number of posts to fetch", "default": 10},
            },
            "required": ["account"],
        },
        "candidate_topics": ["publication_retrieval", "menu_retrieval"],
    },
    {
        "name": "SemanticOpinionFetcherAgent",
        "description": (
            "Fetches comments made by profiles over publications, "
            "including likes and publication date. Supports optional date range filtering."
        ),
        "uri": "local://madro/retrieval_agents/semantic_opinion_fetcher",
        "mcp_schema": {
            "type": "object",
            "properties": {
                "sample": {"type": "integer", "description": "Max number of comments to fetch", "default": 10},
                "date_from": {"type": "string", "description": "Filter comments published on or after this date (ISO 8601)"},
                "date_to": {"type": "string", "description": "Filter comments published on or before this date (ISO 8601)"},
            },
            "required": [],
        },
        "candidate_topics": ["opinion_retrieval", "comment_retrieval"],
    },
    {
        "name": "ImageFetcherAgent",
        "description": (
            "Fetches images and media files associated with Instagram publications, "
            "ordered by publication date and media position. Supports optional date range filtering."
        ),
        "uri": "local://madro/retrieval_agents/image_fetcher_agent",
        "mcp_schema": {
            "type": "object",
            "properties": {
                "sample": {"type": "integer", "description": "Max number of files to fetch", "default": 10},
                "date_from": {"type": "string", "description": "Filter files from publications on or after this date (ISO 8601)"},
                "date_to": {"type": "string", "description": "Filter files from publications on or before this date (ISO 8601)"},
            },
            "required": [],
        },
        "candidate_topics": ["image_retrieval", "media_retrieval"],
        "modality": "image",
    },
]


async def _seed_agent(spec: dict) -> Agent:
    agent, created = await Agent.objects.aget_or_create(
        name=spec["name"],
        defaults={
            "description": spec["description"],
            "uri": spec["uri"],
            "mcp_schema": spec["mcp_schema"],
            "candidate_topics": spec["candidate_topics"],
            "modality": spec.get("modality", "text"),
        },
    )
    if created:
        topics = await categorize_and_assign(agent)
        print(f"  Created  : {agent.name} → topics: {[t.name for t in topics]}")
    else:
        topics = [
            at.topic
            async for at in AgentTopic.objects.filter(agent=agent).select_related("topic")
        ]
        if not topics:
            topics = await categorize_and_assign(agent)
            print(f"  Re-categorized: {agent.name} → topics: {[t.name for t in topics]}")
        else:
            print(f"  Exists   : {agent.name} → topics: {[t.name for t in topics]}")
    return agent


async def seed_agents() -> None:
    for spec in SEED_AGENTS:
        await _seed_agent(spec)
