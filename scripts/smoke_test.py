#!/usr/bin/env python
"""
End-to-end flow smoke test.

Exercises the full pipeline against real data:
  Step 0: seed ProfileFetcherAgent + PublicationFetcherAgent via categorize_and_assign
  Step 1: POST /thread → InteractiveAgent → DemandCategorizationAgent → Publisher
  Step 2: inspect published JobExecution rows
  Step 3: AgentRunner — invoke each agent against RETRIEVAL_DB, normalize, persist

Usage:
    poetry run python scripts/smoke_test.py
"""
import asyncio
import os
import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "madro.settings")
django.setup()

import json
from madro.models import ExecutionStatus, JobExecution, JobStatus
from madro.seed_agents import seed_agents
from madro.workflows.thread_workflow import run_thread
from madro.broker.agent_runner import AgentRunner
from madro.aggregation.relevance_ranker import RelevanceRanker
from madro.aggregation.response_synthesis import ResponseSynthesisAgent


# ---------------------------------------------------------------------------
# Task
# ---------------------------------------------------------------------------
TASK_PROMPTS = [
    "Can you show me Italian restaurants and their respective location?",
    "Show me recent photos and menus from restaurants nearby, including any visible dishes or specials.",
]


def _print_section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print("─" * 60)


async def main() -> None:
    # ------------------------------------------------------------------
    # Step 0: seed agents
    # ------------------------------------------------------------------
    _print_section("Step 0 · Seed agents")
    await seed_agents()

    for task_prompt in TASK_PROMPTS:
        await _run_task(task_prompt)

    _print_section("Done")


async def _run_task(task_prompt: str) -> None:
    # ------------------------------------------------------------------
    # Step 1: run_thread
    # ------------------------------------------------------------------
    _print_section(f"Step 1 · run_thread")
    print(f"Prompt: {task_prompt!r}\n")

    thread, user_message, decomposed = await run_thread(
        thread_id=None,
        task_prompt=task_prompt,
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

    # publisher.py points each JobExecution.demand at its own per-sub-demand
    # Message (focused, localized text) — not at user_message, the raw
    # pre-decomposition prompt — so jobs are found via thread instead.
    jobs = [
        job async for job in JobExecution.objects.filter(
            thread=thread
        ).select_related("agent", "demand")
    ]

    print(f"Jobs published: {len(jobs)}")
    for job in jobs:
        print(f"  · job_id={job.job_id}  agent={job.agent.name}")

    # ------------------------------------------------------------------
    # Step 3: AgentRunner (simulated per agent)
    # ------------------------------------------------------------------
    _print_section("Step 3 · AgentRunner")

    runner = AgentRunner()

    for job in jobs:
        print(f"\nProcessing agent={job.agent.name}")

        job_status = await JobStatus.objects.acreate(
            job=job,
            agent=job.agent,
            status=ExecutionStatus.PROCESSING,
        )

        try:
            artifact = await runner.invoke(job)
            await runner.persist_artifact(job_status, artifact)

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
    _print_section("Step 4 · Relevance ranking")

    ranked = await RelevanceRanker().rank(str(thread.id))

    print(f"Entities ranked: {len(ranked)}\n")
    for i, entity in enumerate(ranked, 1):
        print(f"  #{i} entity_id={entity.entity_id}")
        print(f"     S_lex={entity.s_lex:.4f}  S_sem={entity.s_sem:.4f}  S_relevance={entity.s_relevance:.4f}")
        preview = {k: v for k, v in list(entity.entity_data.items())[:4]}
        print(f"     data={json.dumps(preview, ensure_ascii=False)}")

    # ------------------------------------------------------------------
    # Step 5: Response Synthesis
    # ------------------------------------------------------------------
    _print_section("Step 5 · Response Synthesis")

    response = await ResponseSynthesisAgent().synthesize(str(thread.id), task_prompt)
    print(response)


if __name__ == "__main__":
    asyncio.run(main())
