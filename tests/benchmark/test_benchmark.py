"""
MADRO end-to-end benchmark, reported via Allure.

Not a correctness test suite: it runs the full pipeline against the real
databases (DATABASE_URL for threads/jobs, RETRIEVAL_DB_URL for retrieval
data) for each question in questions.json and attaches evidence (ranked
entities, synthesized answer, etc.) to the Allure report for manual
comparison across runs.

Run with:
    make benchmark

pytest-django is intentionally disabled for this run (see Makefile: `-p no:django`)
because this suite hits the real dev databases directly — see
docs/plan discussion in the "Key constraint" section for why.
"""

import asyncio
import json
from pathlib import Path

import allure
import pytest

from madro.aggregation.relevance_ranker import RelevanceRanker
from madro.aggregation.response_synthesis import ResponseSynthesisAgent
from madro.broker.agent_runner import AgentRunner
from madro.models import ExecutionStatus, JobExecution, JobStatus
from madro.seed_agents import seed_agents
from madro.workflows.thread_workflow import run_thread

pytestmark = pytest.mark.benchmark

QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text())


@pytest.fixture(scope="session")
def event_loop():
    """
    One event loop shared across the whole benchmark session. Each parametrized
    question used to get its own asyncio.run() call — a fresh loop per test —
    but pydantic_ai's Google GenAI client caches an async HTTP client tied to
    whichever loop created it, so a second, unrelated event loop crashes on
    cleanup with "RuntimeError: Event loop is closed" (see smoke_test.py,
    which sidesteps this by wrapping its whole multi-prompt loop in a single
    asyncio.run(main())).
    """
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
def _seeded_agents(event_loop):
    event_loop.run_until_complete(seed_agents())


async def _run_question(prompt: str) -> None:
    with allure.step("Run thread"):
        thread, user_message, decomposed = await run_thread(thread_id=None, task_prompt=prompt)
        allure.attach(
            json.dumps(
                [{"topic": sd.topic_name, "demand": sd.demand} for sd in decomposed.sub_demands],
                ensure_ascii=False,
                indent=2,
            ),
            name="Sub-demands",
            attachment_type=allure.attachment_type.JSON,
        )

    assert thread.id is not None
    assert user_message.id is not None

    with allure.step("Job queue"):
        jobs = [
            job
            async for job in JobExecution.objects.filter(demand=user_message).select_related(
                "agent", "demand"
            )
        ]
        allure.attach(
            json.dumps(
                [{"job_id": str(job.job_id), "agent": job.agent.name} for job in jobs],
                ensure_ascii=False,
                indent=2,
            ),
            name="Published jobs",
            attachment_type=allure.attachment_type.JSON,
        )

    with allure.step("Agent runner"):
        runner = AgentRunner()
        artifact_summaries = []

        for job in jobs:
            job_status = await JobStatus.objects.acreate(
                job=job, agent=job.agent, status=ExecutionStatus.PROCESSING
            )
            try:
                artifact = await runner.invoke(job)
                await runner.persist_artifact(job_status, artifact)
                job_status.status = ExecutionStatus.COMPLETED
                artifact_summaries.append({
                    "agent": job.agent.name,
                    "status": job_status.status,
                    "chunks": len(artifact.semantic_index.chunks),
                    "provenance": artifact.provenance,
                })
            except Exception as exc:
                job_status.status = ExecutionStatus.FAILED
                artifact_summaries.append({
                    "agent": job.agent.name,
                    "status": job_status.status,
                    "error": str(exc),
                })
            finally:
                await job_status.asave(update_fields=["status"])

        allure.attach(
            json.dumps(artifact_summaries, ensure_ascii=False, indent=2),
            name="Artifacts",
            attachment_type=allure.attachment_type.JSON,
        )

    with allure.step("Relevance ranking"):
        ranked = await RelevanceRanker().rank(str(thread.id), prompt)
        allure.attach(
            json.dumps(
                [
                    {
                        "entity_id": e.entity_id,
                        "s_lex": e.s_lex,
                        "s_sem": e.s_sem,
                        "s_relevance": e.s_relevance,
                        "entity_data": e.entity_data,
                    }
                    for e in ranked
                ],
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            name="Ranked entities",
            attachment_type=allure.attachment_type.JSON,
        )

    with allure.step("Response synthesis"):
        response = await ResponseSynthesisAgent().synthesize(str(thread.id), prompt)
        allure.attach(response, name="Synthesized answer", attachment_type=allure.attachment_type.TEXT)

    assert response


@allure.epic("MADRO")
@allure.feature("Benchmark")
@pytest.mark.parametrize("question", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_benchmark_question(question: dict, event_loop) -> None:
    allure.dynamic.title(question["prompt"])
    allure.dynamic.story(question["id"])
    event_loop.run_until_complete(_run_question(question["prompt"]))
