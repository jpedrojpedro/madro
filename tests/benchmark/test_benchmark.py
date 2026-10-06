"""
MADRO end-to-end benchmark, reported via Allure.

Not a correctness test suite: it runs the full pipeline against the real
databases (DATABASE_URL for threads/jobs, RETRIEVAL_DB_URL for retrieval
data) for each question in questions.json and attaches evidence (ranked
entities, synthesized answer, etc.) to the Allure report for manual
comparison across runs.

Run with:
    make benchmark SAMPLE=25 FUSION_LEX=0.3 FUSION_SEM=0.7

pytest-django is intentionally disabled for this run (see Makefile: `-p no:django`)
because this suite hits the real dev databases directly: the schema is
unmanaged and the scraped source data can't be recreated by migrations.
"""

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import allure
import pytest
from pydantic_ai.exceptions import ContentFilterError

from madro.aggregation.relevance_ranker import RelevanceRanker
from madro.aggregation.response_synthesis import ResponseSynthesisAgent
from madro.broker.agent_runner import AgentRunner
from madro.internal_agents.enrichment_agent import EnrichmentAgent
from madro.models import ExecutionStatus, JobExecution, JobStatus
from madro.seed_agents import seed_agents
from madro.workflows.normalizer import MultimodalNormalizer
from madro.workflows.thread_workflow import run_thread

pytestmark = pytest.mark.benchmark

QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text())

# Set by `make benchmark ... K="34 to 50"` to resume a run that stopped
# partway through — slices QUESTIONS to the inclusive id range instead of
# running the full set.
_question_range = os.environ.get("BENCHMARK_QUESTION_RANGE")
if _question_range:
    _start, _, _end = _question_range.partition(" to ")
    _start, _end = int(_start), int(_end.strip() or _start)
    _wanted_ids = {f"Q{n:02d}" for n in range(_start, _end + 1)}
    QUESTIONS = [q for q in QUESTIONS if q["id"] in _wanted_ids]

# Set by the `make benchmark SAMPLE=... FUSION_LEX=... FUSION_SEM=...` target —
# mandatory, so this raises a clear KeyError if run outside that target.
# SAMPLE=-1 is the human-facing spelling for "no limit" — converted to None
# here, at the edge, so nothing downstream (agent_runner, RetrievalAgent,
# NaiveSQLBaseline) has to know about the -1 convention.
_raw_sample = os.environ["BENCHMARK_SAMPLE"]
SAMPLE = None if _raw_sample == "-1" else int(_raw_sample)
ALPHA = float(os.environ["BENCHMARK_ALPHA"])
BETA = float(os.environ["BENCHMARK_BETA"])

# The run's label is built from those args (rather than set manually) and
# attached as Allure parameters, so different runs show up as distinct
# results instead of collapsing into "retries" of the same question.
RUN_LABEL = f"sample-{SAMPLE}_alpha-{ALPHA}_beta-{BETA}"
# Overridable so a resumed/continued run (e.g. after an OOM kill, re-run with
# `-k` selecting only the remaining questions) can reuse the original
# timestamp and land back in the same Allure parent_suite instead of opening
# a new one.
RUN_TIMESTAMP = os.environ.get("BENCHMARK_RUN_TIMESTAMP") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
RUN_ID = f"{RUN_LABEL} @ {RUN_TIMESTAMP}"


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


@dataclass
class Pipeline:
    runner: AgentRunner
    ranker: RelevanceRanker
    synthesizer: ResponseSynthesisAgent


@pytest.fixture(scope="session")
def pipeline() -> Pipeline:
    """
    Built once and reused for every question. EnrichmentAgent calls its vision
    model over Ollama (out-of-process), but MultimodalNormalizer still lazily
    loads a sentence-transformers encoder in-process on first use — one
    instance per question means one encoder reload per question, which grows
    memory unboundedly over a 20-question run until the OS OOM-kills the process.
    """
    normalizer = MultimodalNormalizer()
    runner = AgentRunner(enrichment_agent=EnrichmentAgent(), normalizer=normalizer)
    # top_k=SAMPLE: keeps ranking's output cap in step with the SQL layer's own
    # LIMIT, so raising SAMPLE actually raises how many results MADRO can
    # surface, not just how many candidates it evaluates before truncating back
    # down to a stale default — see relevance_ranker.py's __init__.
    ranker = RelevanceRanker(alpha=ALPHA, beta=BETA, normalizer=normalizer, top_k=SAMPLE)
    synthesizer = ResponseSynthesisAgent(ranker=ranker)
    return Pipeline(runner=runner, ranker=ranker, synthesizer=synthesizer)


def _effective_prompt(question: dict) -> str:
    """`rephrase` stands in for a clarification turn MADRO doesn't implement
    yet — used in place of `prompt` whenever present, uniformly across
    Ground Truth, Baseline, and MADRO."""
    return question.get("rephrase") or question["prompt"]


async def _run_question(prompt: str, pipeline: Pipeline) -> None:
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
        # publisher.py creates one Message per sub-demand (its own focused,
        # localized text) and points each JobExecution.demand at that — not at
        # user_message, the raw pre-decomposition prompt — so jobs for this
        # question are found via thread, the relationship every sub-demand's
        # jobs still share.
        jobs = [
            job
            async for job in JobExecution.objects.filter(thread=thread).select_related(
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
        artifact_summaries = []

        for job in jobs:
            job_status = await JobStatus.objects.acreate(
                job=job, agent=job.agent, status=ExecutionStatus.PROCESSING
            )
            try:
                artifact = await pipeline.runner.invoke(job, sample=SAMPLE)
                await pipeline.runner.persist_artifact(job_status, artifact)
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
        ranked = await pipeline.ranker.rank(str(thread.id))
        allure.attach(
            json.dumps(
                [
                    {
                        "entity_id": e.entity_id,
                        "s_lex": e.s_lex,
                        "s_sem": e.s_sem,
                        "s_relevance": e.s_relevance,
                        "sub_demands": e.sub_demands,
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
        # Ranking above already completed and attached "Ranked entities" — the
        # precision/recall comparison (scripts/compare_baseline.py) is computed
        # from that attachment, not from this synthesized text, so a content
        # filter block here doesn't invalidate the question's score. Recorded
        # as a normal (truthy) answer rather than left to error the test, so a
        # real synthesis bug elsewhere doesn't get masked by this catch.
        try:
            response = await pipeline.synthesizer.synthesize(str(thread.id), prompt)
        except ContentFilterError as exc:
            response = f"[Response synthesis blocked by content filter: {exc}]"
        allure.attach(response, name="Synthesized answer", attachment_type=allure.attachment_type.TEXT)

    assert response


@allure.epic("MADRO")
@pytest.mark.parametrize("question", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_benchmark_question(question: dict, event_loop, pipeline: Pipeline) -> None:
    allure.dynamic.title(question["prompt"])
    allure.dynamic.feature(question["complexity"])
    allure.dynamic.story(question["id"])
    allure.dynamic.tag(question["complexity"])
    allure.dynamic.parameter("approach", RUN_LABEL)
    allure.dynamic.parameter("run_at", RUN_TIMESTAMP)
    # Suites tree (independent of Behaviors above): run -> complexity -> question,
    # since Allure has no single tree deeper than 3 levels.
    allure.dynamic.parent_suite(RUN_ID)
    allure.dynamic.suite(question["complexity"])
    allure.dynamic.sub_suite(question["id"])
    event_loop.run_until_complete(_run_question(_effective_prompt(question), pipeline))
