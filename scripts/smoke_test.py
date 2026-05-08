#!/usr/bin/env python
"""
End-to-end flow smoke test.

Simulates the full pipeline:
  Step 0: seed agent + topic via categorize_and_assign
  Step 1: POST /thread → InteractiveAgent → DemandCategorizationAgent → Publisher
  Step 2: inspect published JobExecution rows
  Step 3: simulate AgentRunner with a hardcoded agent response

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
from madro.workflows.normalizer import normalise, NormalisedArtifact
from madro.workflows.agent_runner import _mean_embedding, _persist_artifact

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
SIMULATED_AGENT_RESPONSE = {
    "result": (
        "EXECUTIVE MENU from Monday to Friday\n\n"
        "STARTERS\nHam and Brie Croquette\nSpicy sauce\n\n"
        "Mini Greek Salad\nFeta cheese, black olive, cucumber, "
        "fresh leaves, crispy pita bread, tzatziki yogurt sauce\n\n"
        "MAIN COURSE\nGrilled St. Pierre\nVegetable panache and broccoli rice\n\n"
        "Crispy Chicken\nCorn cream and mixed greens\n\n"
        "Sweet Potato Gnocchi\nIn sage butter and parmesan cheese"
    ),
    "provenance": {
        "source_table": "instagram_highlights",
        "source_column": "media_text_ocr",
        "highlight_name": "executive",
        "media_type": "image",
        "extraction_agent": "ImageTextExtractorAgent",
    },
}

TASK_PROMPT = "What is on the executive menu this week at the restaurant?"

SEED_AGENT = {
    "name": "InstagramMenuFetcher",
    "description": "Fetches menu information from Instagram highlights of a restaurant account, extracting text from images via OCR.",
    "uri": "local://madro/retrieval_agents/instagram_menu.py",
    "mcp_schema": {
        "type": "object",
        "properties": {
            "account": {"type": "string", "description": "Instagram account handle"},
            "highlight": {"type": "string", "description": "Highlight reel name"},
        },
        "required": ["account"],
    },
    "candidate_topics": ["menu_retrieval", "restaurant", "food"],
}


def _print_section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print("─" * 60)


async def main() -> None:
    # ------------------------------------------------------------------
    # Step 0: seed — register a test agent and assign it to a topic
    # ------------------------------------------------------------------
    _print_section("Step 0 · Seed agent")

    agent, created = await Agent.objects.aget_or_create(
        name=SEED_AGENT["name"],
        defaults={
            "description": SEED_AGENT["description"],
            "uri": SEED_AGENT["uri"],
            "mcp_schema": SEED_AGENT["mcp_schema"],
            "candidate_topics": SEED_AGENT["candidate_topics"],
        },
    )

    if created:
        topics = await categorize_and_assign(agent)
        print(f"Agent created  : {agent.name}")
        print(f"Assigned topics: {[t.name for t in topics]}")
    else:
        topics = [
            at.topic
            async for at in AgentTopic.objects.filter(agent=agent).select_related("topic")
        ]
        if not topics:
            topics = await categorize_and_assign(agent)
            print(f"Agent re-categorized: {agent.name}")
        else:
            print(f"Agent already exists: {agent.name}")
        print(f"Existing topics     : {[t.name for t in topics]}")

    # ------------------------------------------------------------------
    # Step 1: run_thread — interactive agent + demand categorization + publish
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
    # Step 2: fetch the JobExecution rows just published
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
    # Step 3: simulate AgentRunner with hardcoded response
    # ------------------------------------------------------------------
    _print_section("Step 3 · AgentRunner (simulated)")

    for job in jobs:
        print(f"\nProcessing job_id={job.job_id}  agent={job.agent.name}")

        job_status = await JobStatus.objects.acreate(
            job=job,
            agent=job.agent,
            status=ExecutionStatus.PROCESSING,
        )

        try:
            artifact: NormalisedArtifact = await normalise(
                raw=SIMULATED_AGENT_RESPONSE["result"],
                provenance=SIMULATED_AGENT_RESPONSE["provenance"],
            )

            mean_vector = _mean_embedding(artifact.semantic_index.embeddings)
            await _persist_artifact(job_status, artifact, mean_vector)

            job_status.status = ExecutionStatus.COMPLETED
            await job_status.asave(update_fields=["status"])

            print(f"  Status     : {job_status.status}")
            print(f"  Chunks     : {len(artifact.semantic_index.chunks)}")
            print(f"  Embedding  : {artifact.semantic_index.embeddings[0][:4]}... (first 4 dims of chunk 0)")
            print(f"  Lexical    : {artifact.lexical_index.normalization[:80]}...")
            print(f"  Provenance : {json.dumps(artifact.provenance)}")

        except Exception as exc:
            job_status.status = ExecutionStatus.FAILED
            await job_status.asave(update_fields=["status"])
            print(f"  FAILED: {exc}")

    _print_section("Done")


if __name__ == "__main__":
    asyncio.run(main())
