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

from madro.config import get_llama_model, get_model, get_qwen_coder_model, run_agent
from tests.benchmark.baselines.comparison import BASELINE_SUITE_PREFIX
from tests.benchmark.baselines.ground_truth_reference import build_identity_hints
from madro.sql_generation import NaiveSQLBaseline

pytestmark = pytest.mark.baseline

ALLURE_DIR = Path("allure-results")

QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text())

# Every model the naive SQL baseline is run against, keyed by the label that
# shows up as the "baseline_model" Allure parameter and in test ids (e.g.
# "Q01-gemini"). Gemini goes through run_agent (rate-limit throttle + 429
# retry for its free tier) and pydantic_ai's default tool-call output mode.
# Qwen and Llama are served locally via Ollama with no rate limit, and both
# need NativeOutput mode: against the real (long, schema-laden) system
# prompt, both models occasionally add prose around the tool call and break
# JSON parsing — a short smoke-test prompt didn't reproduce it for Llama, but
# the full baseline prompt did (see NaiveSQLBaseline.use_native_output).
BASELINE_MODELS = {
    "gemini": (get_model, run_agent, False),
    "qwen2.5-coder": (get_qwen_coder_model, lambda agent, prompt: agent.run(prompt), True),
    "llama3.1": (get_llama_model, lambda agent, prompt: agent.run(prompt), True),
}

# Same slicing convention as test_benchmark.py, for resuming a partial run.
_question_range = os.environ.get("BENCHMARK_QUESTION_RANGE")
if _question_range:
    _start, _, _end = _question_range.partition(" to ")
    _start, _end = int(_start), int(_end.strip() or _start)
    _wanted_ids = {f"Q{n:02d}" for n in range(_start, _end + 1)}
    QUESTIONS = [q for q in QUESTIONS if q["id"] in _wanted_ids]

RUN_TIMESTAMP = os.environ.get("BASELINE_RUN_TIMESTAMP") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
# NaiveSQLBaseline's own default (10) if unset — see
# `make benchmark-baseline RESULT_LIMIT=100`.
RESULT_LIMIT = int(os.environ.get("BASELINE_RESULT_LIMIT", "10"))


def _run_id(model_key: str) -> str:
    """Same "{label} @ {timestamp}" convention as test_benchmark.py's RUN_ID,
    with the model key standing in for that suite's alpha/beta label — one
    run of `make benchmark-baseline` covers every model in BASELINE_MODELS,
    each getting its own parent_suite under the shared RUN_TIMESTAMP."""
    return f"{BASELINE_SUITE_PREFIX}{model_key} @ {RUN_TIMESTAMP}"


@pytest.fixture(scope="session")
def event_loop():
    """One event loop shared across the whole session — see test_benchmark.py's
    identical fixture for why (pydantic_ai's Google GenAI client caches an
    async HTTP client tied to whichever loop created it)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", params=list(BASELINE_MODELS), ids=list(BASELINE_MODELS))
def baseline(request) -> tuple[str, NaiveSQLBaseline]:
    model_key = request.param
    get_model_fn, runner, use_native_output = BASELINE_MODELS[model_key]
    return model_key, NaiveSQLBaseline(
        model=get_model_fn(), runner=runner, use_native_output=use_native_output, result_limit=RESULT_LIMIT
    )


@pytest.fixture(scope="session")
def identity_hints() -> dict[str, str]:
    """question_id -> identity field name, read off the most recent Ground
    Truth run already sitting in allure-results/ — see
    ground_truth_reference.py. Missing for questions with no Ground Truth
    run yet; NaiveSQLBaseline falls back to its generic instructions in that
    case. Run `make benchmark-ground-truth` first if you want this
    populated."""
    return build_identity_hints(ALLURE_DIR)


def _effective_prompt(question: dict) -> str:
    """`rephrase` stands in for a clarification turn MADRO doesn't implement
    yet — used in place of `prompt` whenever present, uniformly across
    Ground Truth, Baseline, and MADRO. See CONTEXT.md's `effective prompt`."""
    return question.get("rephrase") or question["prompt"]


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
    question: dict, event_loop, baseline: tuple[str, NaiveSQLBaseline], identity_hints: dict[str, str],
) -> None:
    model_key, baseline_instance = baseline
    allure.dynamic.title(question["prompt"])
    allure.dynamic.feature(question["complexity"])
    allure.dynamic.story(question["id"])
    allure.dynamic.tag(question["complexity"])
    allure.dynamic.tag(model_key)
    allure.dynamic.parameter("run_at", RUN_TIMESTAMP)
    allure.dynamic.parameter("baseline_model", model_key)
    # BASELINE_SUITE_PREFIX keeps this distinguishable from test_benchmark.py's
    # and test_ground_truth.py's RUN_ID values (neither of which start with
    # it), so scripts/compare_baseline.py and ground_truth_reference.py can
    # tell the different kinds of run apart while all three live in the same
    # allure-results/ directory.
    allure.dynamic.parent_suite(_run_id(model_key))
    allure.dynamic.suite(question["complexity"])
    allure.dynamic.sub_suite(f"{question['id']} ({model_key})")
    identity_hint = identity_hints.get(question["id"])
    event_loop.run_until_complete(_run_question(_effective_prompt(question), baseline_instance, identity_hint))
