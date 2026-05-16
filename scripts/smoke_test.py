#!/usr/bin/env python
"""
End-to-end flow smoke test.

Exercises the full pipeline against real data:
  Step 0: seed ProfileFetcherAgent + PublicationFetcherAgent via categorize_and_assign
  Step 1: POST /thread → InteractiveAgent → DemandCategorizationAgent → Publisher
  Step 2: inspect published JobExecution rows
  Step 3: AgentRunner — invoke each agent against RETRIEVAL_DB, normalise, persist

Usage:
    poetry run python scripts/smoke_test.py
"""
import asyncio
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "madro.settings")
django.setup()

import json
from madro.models import Agent, AgentTopic, ExecutionStatus, JobExecution, JobStatus
from madro.workflows.topic_categorization_agent import categorize_and_assign
from madro.workflows.thread_workflow import run_thread
from madro.workflows.agent_runner import _persist_artifact
from madro.workflows.retrieval_agent import invoke
from madro.workflows.aggregation.fusion import fuse
from madro.workflows.aggregation.response_synthesis import synthesize


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
TASK_PROMPT = "Can you show me Italian restaurants and their respective location?"

# ---------------------------------------------------------------------------
# Seed agents
# ---------------------------------------------------------------------------
SEED_AGENTS = [
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
]

def _print_section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print("─" * 60)


async def _seed_agent(spec: dict) -> Agent:
    agent, created = await Agent.objects.aget_or_create(
        name=spec["name"],
        defaults={
            "description": spec["description"],
            "uri": spec["uri"],
            "mcp_schema": spec["mcp_schema"],
            "candidate_topics": spec["candidate_topics"],
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


async def main() -> None:
    # ------------------------------------------------------------------
    # Step 0: seed agents
    # ------------------------------------------------------------------
    _print_section("Step 0 · Seed agents")
    for spec in SEED_AGENTS:
        await _seed_agent(spec)

    # ------------------------------------------------------------------
    # Step 1: run_thread
    # ------------------------------------------------------------------
    _print_section("Step 1 · run_thread")
    print(f"Prompt: {TASK_PROMPT!r}\n")

    thread, user_message, decomposed = await run_thread(
        thread_id=None,
        task_prompt=TASK_PROMPT,
    )

    print(f"Thread ID   : {thread.id}")
    print(f"Message ID  : {user_message.id}")
    print(f"Sub-demands :")
    for sd in decomposed.sub_demands:
        print(f"  · [{sd.topic_name}] {sd.demand}")

    # ------------------------------------------------------------------
    # Step 2: JobExecution queue
    # ------------------------------------------------------------------
    _print_section("Step 2 · JobExecution queue (published rows)")

    jobs = [
        job async for job in JobExecution.objects.filter(
            demand=user_message
        ).select_related("agent", "demand")
    ]

    print(f"Jobs published: {len(jobs)}")
    for job in jobs:
        print(f"  · job_id={job.job_id}  agent={job.agent.name}")

    # ------------------------------------------------------------------
    # Step 3: AgentRunner (simulated per agent)
    # ------------------------------------------------------------------
    _print_section("Step 3 · AgentRunner")

    for job in jobs:
        print(f"\nProcessing agent={job.agent.name}")

        job_status = await JobStatus.objects.acreate(
            job=job,
            agent=job.agent,
            status=ExecutionStatus.PROCESSING,
        )

        try:
            artifact = await invoke(job)
            await _persist_artifact(job_status, artifact)

            job_status.status = ExecutionStatus.COMPLETED
            await job_status.asave(update_fields=["status"])

            print(f"  Status     : {job_status.status}")
            print(f"  Chunks     : {len(artifact.semantic_index.chunks)}")
            print(f"  Embedding  : {artifact.semantic_index.embeddings[0][:4]}... (first 4 dims)")
            print(f"  Lexical    : {artifact.lexical_index.normalization[:80]}...")
            print(f"  Provenance : {json.dumps(artifact.provenance)}")

        except Exception as exc:
            job_status.status = ExecutionStatus.FAILED
            await job_status.asave(update_fields=["status"])
            print(f"  FAILED: {exc}")

    # ------------------------------------------------------------------
    # Step 4: Fusion
    # ------------------------------------------------------------------
    _print_section("Step 4 · Fusion ranking")

    ranked = await fuse(str(thread.id), TASK_PROMPT)

    print(f"Entities ranked: {len(ranked)}\n")
    for i, entity in enumerate(ranked, 1):
        print(f"  #{i} entity_id={entity.entity_id}")
        print(f"     S_text={entity.s_text:.4f}  S_image={entity.s_image:.4f}  S_fusion={entity.s_fusion:.4f}")
        preview = {k: v for k, v in list(entity.entity_data.items())[:4]}
        print(f"     data={json.dumps(preview, ensure_ascii=False)}")

    # ------------------------------------------------------------------
    # Step 5: Response Synthesis
    # ------------------------------------------------------------------
    _print_section("Step 5 · Response Synthesis")

    response = await synthesize(str(thread.id), TASK_PROMPT)
    print(response)

    _print_section("Done")


if __name__ == "__main__":
    asyncio.run(main())
