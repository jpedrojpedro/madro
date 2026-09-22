"""
Ground Truth: the authoritative answer for each benchmark question. Runs the
same zero-shot SQL resolver as Baseline (`NaiveSQLBaseline`), Gemini only,
but scoped to only the tables named in that question's `hint` — see
`hint_schema.build_hint_schema()` and CONTEXT.md's `hint`/`Ground Truth`
entries for why this scoping is exclusive to Ground Truth.

Run with:
    make benchmark-ground-truth
"""

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import allure
import pytest

from madro.config import get_model, run_agent
from tests.benchmark.baselines.comparison import GROUND_TRUTH_SUITE_PREFIX
from tests.benchmark.baselines.hint_schema import build_hint_schema
from madro.sql_generation import NaiveSQLBaseline

pytestmark = pytest.mark.ground_truth

ALLURE_DIR = Path("allure-results")

QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text())

# Same slicing convention as test_benchmark.py/test_baseline.py, for
# resuming a partial run.
_question_range = os.environ.get("BENCHMARK_QUESTION_RANGE")
if _question_range:
    _start, _, _end = _question_range.partition(" to ")
    _start, _end = int(_start), int(_end.strip() or _start)
    _wanted_ids = {f"Q{n:02d}" for n in range(_start, _end + 1)}
    QUESTIONS = [q for q in QUESTIONS if q["id"] in _wanted_ids]

RUN_TIMESTAMP = os.environ.get("GROUND_TRUTH_RUN_TIMESTAMP") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
# NaiveSQLBaseline's own default (10) if unset — see
# `make benchmark-ground-truth RESULT_LIMIT=100`.
RESULT_LIMIT = int(os.environ.get("GROUND_TRUTH_RESULT_LIMIT", "10"))
# Keeps the "gemini" segment even though there's only one model today, so
# this stays parseable the same way as Baseline's "{label} @ {timestamp}"
# parent_suite if a second Ground Truth model is ever added.
RUN_ID = f"{GROUND_TRUTH_SUITE_PREFIX}gemini @ {RUN_TIMESTAMP}"


@pytest.fixture(scope="session")
def event_loop():
    """Same rationale as test_baseline.py's identical fixture — pydantic_ai's
    Google GenAI client caches an async HTTP client tied to whichever loop
    created it."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _effective_prompt(question: dict) -> str:
    """`rephrase` stands in for a clarification turn MADRO doesn't implement
    yet — used in place of `prompt` whenever present, uniformly across
    Ground Truth, Baseline, and MADRO. See CONTEXT.md's `effective prompt`."""
    return question.get("rephrase") or question["prompt"]


async def _run_question(question: dict) -> None:
    # Built fresh per question: each question's `hint` names a different
    # subset of tables, so the schema (and therefore the Agent's system
    # prompt) can't be shared across questions the way Baseline's can.
    schema_doc = build_hint_schema(question["hint"])
    ground_truth = NaiveSQLBaseline(
        model=get_model(), runner=run_agent, schema_doc=schema_doc, result_limit=RESULT_LIMIT
    )
    outcome = await ground_truth.resolve(_effective_prompt(question))
    allure.attach(outcome.sql, name="Ground Truth SQL", attachment_type=allure.attachment_type.TEXT)
    payload = {"rows": outcome.rows} if outcome.error is None else {"error": outcome.error}
    allure.attach(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        name="Ground Truth result",
        attachment_type=allure.attachment_type.JSON,
    )
    allure.attach(
        json.dumps(
            {
                "hint": question["hint"],
                "attempts": outcome.attempts,
                "failed_attempts": [{"sql": h.sql, "error": h.error} for h in outcome.history],
            },
            ensure_ascii=False, indent=2, default=str,
        ),
        name="Ground Truth attempts",
        attachment_type=allure.attachment_type.JSON,
    )


@allure.epic("MADRO")
@pytest.mark.parametrize("question", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_ground_truth_question(question: dict, event_loop) -> None:
    allure.dynamic.title(question["prompt"])
    allure.dynamic.feature(question["complexity"])
    allure.dynamic.story(question["id"])
    allure.dynamic.tag(question["complexity"])
    allure.dynamic.parameter("run_at", RUN_TIMESTAMP)
    # GROUND_TRUTH_SUITE_PREFIX keeps this distinguishable from
    # test_benchmark.py's/test_baseline.py's parent_suite values in the same
    # allure-results/ directory — see comparison.py.
    allure.dynamic.parent_suite(RUN_ID)
    allure.dynamic.suite(question["complexity"])
    allure.dynamic.sub_suite(question["id"])
    event_loop.run_until_complete(_run_question(question))
