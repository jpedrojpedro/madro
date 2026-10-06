"""
Reference-100: the paper's single Ground Truth run, rebuilt from the
2026-08-28 Ground Truth run instead of regenerated from scratch.

For every question, the reference SQL is taken verbatim from
SOURCE_ALLURE_DIR's run, except the questions in REGENERATED (whose
`rephrase` changed after that run), which are regenerated under the same
protocol that run used: same model, no temperature override, and
`LIMIT 10` asked for in the prompt. Every SQL then gets its trailing
`LIMIT 10` rewritten to `LIMIT 100` (a model-chosen `LIMIT 1` is kept) and is
executed read-only against dowser.

Run with:
    make benchmark-reference-rerun
"""

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import allure
import pytest
from django.conf import settings
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from madro.config import load_config, run_agent
from madro.sql_generation import NaiveSQLBaseline
from tests.benchmark.baselines.comparison import GROUND_TRUTH_SUITE_PREFIX, identity
from tests.benchmark.baselines.hint_schema import build_hint_schema

pytestmark = pytest.mark.reference_rerun

QUESTIONS = json.loads((Path(__file__).parent / "questions.json").read_text())

SOURCE_ALLURE_DIR = Path("allure-results-thesis")
SOURCE_RUN_AT = "2026-08-28T21:25:17Z"
# Questions whose `rephrase` changed in questions.json after SOURCE_RUN_AT
# (commit e2a6740) — their source SQL answers a different effective prompt.
# Q12's rephrase was reverted instead: it steered every generation towards a
# literal 'Japanese' category that doesn't exist, while its source SQL answers
# the original prompt with a non-empty result.
REGENERATED = {"Q01", "Q13", "Q15", "Q17", "Q26", "Q29", "Q35", "Q44", "Q46"}
SOURCE_LIMIT = 10
TARGET_LIMIT = 100
STATEMENT_TIMEOUT_MS = NaiveSQLBaseline.STATEMENT_TIMEOUT_MS

_TRAILING_LIMIT = re.compile(rf"(?i)\bLIMIT\s+{SOURCE_LIMIT}(\s*;?\s*)$")

_question_range = os.environ.get("BENCHMARK_QUESTION_RANGE")
if _question_range:
    _start, _, _end = _question_range.partition(" to ")
    _start, _end = int(_start), int(_end.strip() or _start)
    _wanted_ids = {f"Q{n:02d}" for n in range(_start, _end + 1)}
    QUESTIONS = [q for q in QUESTIONS if q["id"] in _wanted_ids]

RUN_TIMESTAMP = os.environ.get("GROUND_TRUTH_RUN_TIMESTAMP") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
RUN_ID = f"{GROUND_TRUTH_SUITE_PREFIX}gemini @ {RUN_TIMESTAMP}"


@pytest.fixture(scope="session")
def event_loop():
    """Same rationale as test_ground_truth.py's identical fixture."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


def _source_entries() -> dict[str, dict]:
    """question_id -> {"sql", "rows"} from SOURCE_ALLURE_DIR's SOURCE_RUN_AT run."""
    entries: dict[str, dict] = {}
    for path in SOURCE_ALLURE_DIR.glob("*-result.json"):
        data = json.loads(path.read_text())
        labels = {label["name"]: label["value"] for label in data.get("labels", [])}
        if labels.get("parentSuite") != f"{GROUND_TRUTH_SUITE_PREFIX}gemini @ {SOURCE_RUN_AT}":
            continue
        attachments = {a["name"]: SOURCE_ALLURE_DIR / a["source"] for a in data.get("attachments", [])}
        entries[labels["story"]] = {
            "sql": attachments["Ground Truth SQL"].read_text(),
            "rows": json.loads(attachments["Ground Truth result"].read_text()).get("rows"),
        }
    return entries


SOURCE = _source_entries()


def _source_protocol_model() -> GoogleModel:
    """get_model() as it was at SOURCE_RUN_AT — no `settings`, so the
    provider's default temperature applies rather than configs' `temperature`."""
    provider = GoogleProvider(api_key=os.environ["GOOGLE_API_KEY"])
    return GoogleModel(load_config().model.name, provider=provider)


def _effective_prompt(question: dict) -> str:
    return question.get("rephrase") or question["prompt"]


def _identities(rows: list[dict] | None) -> list[str]:
    keys = (identity(row) for row in rows or [])
    return [f"{key[0]}={key[1]}" for key in keys if key]


async def _execute(sql_text: str) -> list[dict]:
    conn = await AsyncConnection.connect(settings.RETRIEVAL_DB_URL, row_factory=dict_row)
    try:
        async with conn.cursor() as cur:
            await cur.execute("SET TRANSACTION READ ONLY")
            await cur.execute(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
            await cur.execute(sql_text)
            return await cur.fetchall()
    finally:
        await conn.rollback()
        await conn.close()


async def _run_question(question: dict) -> None:
    question_id = question["id"]
    provenance: dict = {"source_run_at": SOURCE_RUN_AT, "target_limit": TARGET_LIMIT}
    attempts: dict = {"hint": question["hint"]}

    if question_id in REGENERATED:
        generator = NaiveSQLBaseline(
            model=_source_protocol_model(), runner=run_agent,
            schema_doc=build_hint_schema(question["hint"]), result_limit=SOURCE_LIMIT,
        )
        outcome = await generator.resolve(_effective_prompt(question))
        source_sql, generation_error = outcome.sql, outcome.error
        provenance["source"] = "regenerated"
        attempts["attempts"] = outcome.attempts
        attempts["failed_attempts"] = [{"sql": h.sql, "error": h.error} for h in outcome.history]
    else:
        source_sql, generation_error = SOURCE[question_id]["sql"], None
        provenance["source"] = "source_run"

    sql_text, rewritten = _TRAILING_LIMIT.subn(rf"LIMIT {TARGET_LIMIT}\1", source_sql.strip())
    provenance["source_sql"] = source_sql
    provenance["limit_rewritten"] = bool(rewritten)

    if generation_error is not None:
        payload = {"error": generation_error}
    else:
        try:
            rows = (await _execute(sql_text))[:TARGET_LIMIT]
            payload = {"rows": rows}
        except Exception as exc:
            payload = {"error": str(exc)}

    if provenance["source"] == "source_run" and "rows" in payload:
        # Same SQL with a larger LIMIT: its first SOURCE_LIMIT identities
        # should be exactly the source run's, barring ORDER BY ties or data
        # that changed in dowser since SOURCE_RUN_AT.
        source_ids = _identities(SOURCE[question_id]["rows"])
        new_prefix = _identities(payload["rows"])[: len(source_ids)] if source_ids else []
        provenance["source_identities"] = source_ids
        provenance["prefix_matches_source"] = new_prefix == source_ids
        provenance["prefix_set_matches_source"] = set(new_prefix) == set(source_ids)

    allure.attach(sql_text, name="Ground Truth SQL", attachment_type=allure.attachment_type.TEXT)
    allure.attach(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        name="Ground Truth result",
        attachment_type=allure.attachment_type.JSON,
    )
    allure.attach(
        json.dumps(attempts, ensure_ascii=False, indent=2, default=str),
        name="Ground Truth attempts",
        attachment_type=allure.attachment_type.JSON,
    )
    allure.attach(
        json.dumps(provenance, ensure_ascii=False, indent=2, default=str),
        name="Reference provenance",
        attachment_type=allure.attachment_type.JSON,
    )


@allure.epic("MADRO")
@pytest.mark.parametrize("question", QUESTIONS, ids=[q["id"] for q in QUESTIONS])
def test_reference_rerun_question(question: dict, event_loop) -> None:
    allure.dynamic.title(question["prompt"])
    allure.dynamic.feature(question["complexity"])
    allure.dynamic.story(question["id"])
    allure.dynamic.tag(question["complexity"])
    allure.dynamic.tag("reference-100")
    allure.dynamic.parameter("run_at", RUN_TIMESTAMP)
    allure.dynamic.parent_suite(RUN_ID)
    allure.dynamic.suite(question["complexity"])
    allure.dynamic.sub_suite(question["id"])
    event_loop.run_until_complete(_run_question(question))
