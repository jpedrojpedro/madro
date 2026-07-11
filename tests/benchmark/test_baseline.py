"""
Standalone naive-SQL baseline benchmark, reported via Allure.

Runs only `NaiveSQLBaseline.resolve()` per question — no thread workflow, no
agent runner, no VLM enrichment, no relevance ranker — so it's cheap to
(re)run independently of a full `make benchmark` pass. Use
scripts/compare_baseline.py to compare its results against an existing
MADRO run's Allure output, rather than re-running that pipeline.

Run with:
    make benchmark-baseline
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import allure
import pytest

from tests.benchmark.baselines.madro_reference import build_identity_hints
from tests.benchmark.baselines.naive_sql_baseline import NaiveSQLBaseline

pytestmark = pytest.mark.baseline

ALLURE_DIR = Path("allure-results")

QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text())

# Same slicing convention as test_benchmark.py, for resuming a partial run.
_question_range = os.environ.get("BENCHMARK_QUESTION_RANGE")
if _question_range:
    _start, _, _end = _question_range.partition(" to ")
    _start, _end = int(_start), int(_end.strip() or _start)
    _wanted_ids = {f"Q{n:02d}" for n in range(_start, _end + 1)}
    QUESTIONS = [q for q in QUESTIONS if q["id"] in _wanted_ids]

RUN_TIMESTAMP = os.environ.get("BASELINE_RUN_TIMESTAMP") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(scope="session")
def event_loop():
    """One event loop shared across the whole session — see test_benchmark.py's
    identical fixture for why (pydantic_ai's Google GenAI client caches an
    async HTTP client tied to whichever loop created it)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def baseline() -> NaiveSQLBaseline:
    return NaiveSQLBaseline()


@pytest.fixture(scope="session")
def identity_hints() -> dict[str, str]:
    """question_id -> identity field name, computed once from whatever MADRO
    runs already exist in allure-results/ — see madro_reference.py. Missing
    for questions with no prior MADRO run; NaiveSQLBaseline falls back to its
    generic instructions in that case."""
    return build_identity_hints(ALLURE_DIR)


async def _run_question(prompt: str, baseline: NaiveSQLBaseline, identity_hint: str | None) -> None:
    outcome = await baseline.resolve(prompt, identity_hint=identity_hint)
    allure.attach(outcome.sql, name="Baseline SQL", attachment_type=allure.attachment_type.TEXT)
    payload = {"rows": outcome.rows} if outcome.error is None else {"error": outcome.error}
    allure.attach(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        name="Baseline result",
        attachment_type=allure.attachment_type.JSON,
    )
    allure.attach(
        json.dumps(
            {
                "identity_hint": identity_hint,
                "attempts": outcome.attempts,
                "failed_attempts": [{"sql": h.sql, "error": h.error} for h in outcome.history],
            },
            ensure_ascii=False, indent=2, default=str,
        ),
        name="Baseline attempts",
        attachment_type=allure.attachment_type.JSON,
    )


@allure.epic("MADRO")
@pytest.mark.parametrize("question", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_baseline_question(
    question: dict, event_loop, baseline: NaiveSQLBaseline, identity_hints: dict[str, str],
) -> None:
    allure.dynamic.title(question["prompt"])
    allure.dynamic.feature(question["complexity"])
    allure.dynamic.story(question["id"])
    allure.dynamic.tag(question["complexity"])
    allure.dynamic.parameter("run_at", RUN_TIMESTAMP)
    # Distinct parent_suite from test_benchmark.py's RUN_ID values, so
    # scripts/compare_baseline.py can tell the two kinds of run apart while
    # both live in the same allure-results/ directory.
    allure.dynamic.parent_suite("baseline")
    allure.dynamic.suite(question["complexity"])
    allure.dynamic.sub_suite(question["id"])
    identity_hint = identity_hints.get(question["id"])
    event_loop.run_until_complete(_run_question(question["prompt"], baseline, identity_hint))
