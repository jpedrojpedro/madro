"""
Zero-shot one-shot SQL resolver: given only a prompt and a markdown schema
description, have an LLM write a read-only SQL query and execute it,
retrying up to MAX_ATTEMPTS times. Each retry's prompt includes the previous
attempt's SQL and either the execution error or a note that the query
returned zero rows, so the model can self-correct. A final empty result is
still a valid, expected outcome — not every question has an answer
expressible as raw SQL (e.g. it may depend on OCR/image-caption content that
only exists in MADRO's own enrichment pipeline) — but a query that merely
*executed* fine while returning nothing is worth one nudge to loosen an
overly strict filter before accepting that.

Shared by both Baseline (default schema_doc: the full `dowser_schema.md`,
public schema only) and Ground Truth (a per-question hint-scoped schema
slice — see `hint_schema.build_hint_schema()`); the class name reflects its
original, Baseline-only purpose but it's schema-agnostic.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.models import Model
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from django.conf import settings

from madro.config import get_model, run_agent

AgentRunner = Callable[[Agent, str], Awaitable[AgentRunResult]]

SQL_GENERATION_SP = """
You are a naive SQL analyst. Write a single, read-only PostgreSQL query
against the `dowser` database described below, whose result set — taken in
the order you return it — answers the user's request. If your query fails
to execute, or executes but returns zero rows, you will be shown that and
asked to try again, up to a handful of attempts — but still aim to get it
right on the first try, since the number of attempts you need is itself a
signal.

Rules:
- Output exactly one SQL statement: a SELECT, or a WITH ... SELECT. No DDL,
  no DML, no multiple statements.
- Always include whichever identity column(s) apply to what you're
  selecting, aliased EXACTLY as `profile_id`, `publication_id`, or
  `comment_id` — never leave it as the bare `id` column, even when selecting
  directly from `profile`/`publication`/`comment`, since the column name
  itself is how each result row gets matched back to its source record.
  E.g. `SELECT p.id AS profile_id, ...` — not `SELECT p.id, ...`. If you are
  told to identify results using a specific column, use exactly that one
  even if a different one would come more naturally for this query — e.g.
  join back to `profile.id` to expose `profile_id` even when selecting from
  `comment`/`publication`.
- ORDER BY a relevance signal — ts_rank(...) over biography_lexemes /
  description_lexemes when the request is about matching free text;
  otherwise a sensible fallback such as recency (published_at) or
  engagement (num_followers / num_likes / num_comments) — only the top of
  your ordering will be used.
- End with LIMIT 10.
- If the request cannot be answered from this schema (e.g. it depends on
  what is visually shown in an image), still write your best-effort query
  rather than refusing — an empty or irrelevant result is an acceptable
  outcome.

Schema:

{schema}
"""

RETRY_PROMPT_TEMPLATE = """{prompt}

Your previous attempt produced this SQL:
{sql}

Executing it failed with this error:
{error}

Write a corrected single SQL query that fixes this error."""

EMPTY_RESULT_MESSAGE = (
    "Query executed successfully but returned 0 rows — the filter may be "
    "too restrictive. Consider: OR instead of AND between separate concepts, "
    "dropping an overly specific constraint, or a broader tsquery."
)


class SQLGenerationResult(BaseModel):
    sql: str = Field(description="A single read-only PostgreSQL SELECT/WITH statement answering the request.")


@dataclass
class FailedAttempt:
    sql: str
    error: str


@dataclass
class NaiveSQLOutcome:
    sql: str
    rows: list[dict] | None
    error: str | None
    attempts: int
    history: list[FailedAttempt] = field(default_factory=list)


class NaiveSQLBaseline:
    STATEMENT_TIMEOUT_MS = 5_000
    RESULT_LIMIT = 10
    MAX_ATTEMPTS = 5

    def __init__(
        self,
        model: Model | None = None,
        runner: AgentRunner | None = None,
        use_native_output: bool = False,
        schema_doc: str | None = None,
    ):
        # Baseline queries the full public schema (default: dowser_schema.md).
        # Ground Truth passes its own hint-scoped schema slice instead — see
        # hint_schema.build_hint_schema().
        schema_doc = schema_doc or (Path(__file__).parent / "dowser_schema.md").read_text()
        # Gemini reliably returns SQLGenerationResult via pydantic_ai's default
        # tool-call output mode. Qwen2.5-Coder (served locally via Ollama) does
        # not — it tends to emit extra prose/SQL alongside the tool call,
        # breaking JSON parsing — but handles NativeOutput's response_format
        # json_schema mode correctly, so non-Gemini models should pass
        # use_native_output=True.
        output_type = NativeOutput(SQLGenerationResult) if use_native_output else SQLGenerationResult
        self._agent = Agent(
            model=model or get_model(),
            output_type=output_type,
            system_prompt=SQL_GENERATION_SP.format(schema=schema_doc),
        )
        # run_agent applies Gemini's free-tier rate-limit throttle/retry — not
        # applicable to a locally-served model, so callers using a non-Gemini
        # model should pass their own runner (e.g. a plain `Agent.run`).
        self._runner: AgentRunner = runner or run_agent

    async def resolve(self, prompt: str, identity_hint: str | None = None) -> NaiveSQLOutcome:
        history: list[FailedAttempt] = []
        base_prompt = self._build_prompt(prompt, identity_hint)
        current_prompt = base_prompt

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            result = await self._runner(self._agent, current_prompt)
            sql_text = result.output.sql.strip()

            error = self._guard(sql_text)
            rows: list[dict] | None = None
            if error is None:
                try:
                    rows = await self._execute(sql_text)
                except Exception as exc:
                    error = str(exc)

            if error is None and rows:
                return NaiveSQLOutcome(
                    sql=sql_text, rows=rows[: self.RESULT_LIMIT], error=None,
                    attempts=attempt, history=history,
                )

            if error is None:
                # Executed fine, just empty — a legitimate outcome, but worth
                # one nudge to rule out an overly strict filter before we
                # accept it, unless this was the last attempt available.
                if attempt == self.MAX_ATTEMPTS:
                    return NaiveSQLOutcome(sql=sql_text, rows=[], error=None, attempts=attempt, history=history)
                error = EMPTY_RESULT_MESSAGE

            if attempt == self.MAX_ATTEMPTS:
                return NaiveSQLOutcome(sql=sql_text, rows=None, error=error, attempts=attempt, history=history)

            history.append(FailedAttempt(sql=sql_text, error=error))
            current_prompt = RETRY_PROMPT_TEMPLATE.format(prompt=base_prompt, sql=sql_text, error=error)

    @staticmethod
    def _build_prompt(prompt: str, identity_hint: str | None) -> str:
        if not identity_hint:
            return prompt
        return (
            f"{prompt}\n\n"
            f"Identify each result row using the `{identity_hint}` column "
            f"— alias it exactly as `{identity_hint}`."
        )

    @staticmethod
    def _guard(sql_text: str) -> str | None:
        stripped = sql_text.strip().rstrip(";")
        if not re.match(r"(?is)^\s*(select|with)\b", stripped):
            return "Generated SQL is not a SELECT/WITH statement — rejected before execution."
        if ";" in stripped:
            return "Generated SQL contains multiple statements — rejected before execution."
        return None

    async def _execute(self, sql_text: str) -> list[dict]:
        conn = await AsyncConnection.connect(settings.RETRIEVAL_DB_URL, row_factory=dict_row)
        try:
            async with conn.cursor() as cur:
                await cur.execute("SET TRANSACTION READ ONLY")
                await cur.execute(f"SET LOCAL statement_timeout = {self.STATEMENT_TIMEOUT_MS}")
                await cur.execute(sql_text)
                return await cur.fetchall()
        finally:
            await conn.rollback()
            await conn.close()
