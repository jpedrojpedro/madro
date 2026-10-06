"""
Zero-shot SQL resolver: given only a prompt and a markdown schema
description, have an LLM write a read-only SQL query and execute it,
retrying up to MAX_ATTEMPTS times. Each retry's prompt includes the previous
attempt's SQL and either the execution error or a note that the query
returned zero rows, so the model can self-correct. A final empty result is
still a valid, expected outcome — not every question has an answer
expressible as raw SQL (e.g. it may depend on OCR/image-caption content that
only exists in MADRO's own enrichment pipeline) — but a query that merely
*executed* fine while returning nothing is worth one nudge to loosen an
overly strict filter before accepting that.

Shared by Baseline (default schema_doc: the full `configs/dowser_schema.md`,
public schema only), Ground Truth (a per-question hint-scoped schema slice —
see `tests/benchmark/baselines/hint_schema.build_hint_schema()`), and every
`RetrievalAgent` (a per-agent `public.*`-only scoped slice — see
`retrieval_agents/schema_scope.build_scoped_schema()`); the class name
reflects its original, Baseline-only purpose but it's schema-agnostic.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, Field
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.agent import AgentRunResult
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from django.conf import settings

from madro.config import get_model, run_agent
from madro.pseudonymization import reveal as reveal_alias

AgentRunner = Callable[[Agent, str], Awaitable[AgentRunResult]]

DOWSER_SCHEMA_PATH = Path(settings.BASE_DIR) / "configs" / "dowser_schema.md"


async def plain_runner(agent: Agent, prompt: str) -> AgentRunResult:
    """No throttling/retry — for locally-served (Ollama) models, which have
    no rate limit or quota to manage, unlike `madro.config.run_agent`."""
    return await agent.run(prompt)


def parse_schema_sections(markdown: str) -> dict[str, str]:
    """`## \\`name\\`` heading text (backtick contents, verbatim) -> that
    section's body. Shared parsing so a schema doc is only ever read one
    way — hint_schema.py and retrieval_agents/schema_scope.py both build
    their scoped slices from this."""
    sections: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []

    def _flush() -> None:
        if current_name is not None:
            sections[current_name] = "\n".join(current_lines).strip()

    for line in markdown.splitlines():
        if line.startswith("## "):
            _flush()
            match = re.search(r"`([^`]+)`", line)
            current_name = match.group(1) if match else None
            current_lines = [line]
        else:
            current_lines.append(line)
    _flush()
    return sections


SQL_GENERATION_SP = """
You are a SQL expert. Write a single, read-only PostgreSQL query
against the database schema described below, whose result set — taken in
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
- Also SELECT whatever descriptive free-text/attribute columns are relevant
  to the request (e.g. biography, description, annotation, category,
  follower counts) — not the identity column alone. The result may be used
  to write a natural-language answer, not just to check which rows matched.
- ORDER BY a relevance signal — ts_rank(...) over biography_lexemes /
  description_lexemes when the request is about matching free text;
  otherwise a sensible fallback such as recency (published_at) or
  engagement (num_followers / num_likes / num_comments) — only the top of
  your ordering will be used. Whenever you order by a computed ts_rank(...)
  value, also SELECT it aliased exactly as `rnk` — it is reused downstream
  as this result's own lexical relevance score.
- Every `*_lexemes` tsvector column uses the custom `pt_en` text search
  configuration. Always write `to_tsquery('pt_en', ...)` against them —
  never `'english'` or `'portuguese'` alone — regardless of whether your
  search terms are in English or Portuguese; querying with a different
  configuration than the column was built with can silently miss matches.
  `to_tsquery` requires explicit `&`/`|` operators between multiple words
  (e.g. `to_tsquery('pt_en', 'italian | restaurant')`) — a plain
  space-separated phrase is a syntax error, not an implicit AND/OR.
{limit}
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
    usage: RunUsage = field(default_factory=RunUsage)
    """Summed across every attempt in this resolve() call, not just the
    final one — a retried SQL generation costs tokens on every attempt."""


class NaiveSQLBaseline:
    STATEMENT_TIMEOUT_MS = 5_000
    MAX_ATTEMPTS = 5

    def __init__(
        self,
        model: Model | None = None,
        runner: AgentRunner | None = None,
        use_native_output: bool = False,
        schema_doc: str | None = None,
        result_limit: int | None = 10,
    ):
        # Baseline queries the full public schema (default: dowser_schema.md).
        # Ground Truth passes its own hint-scoped schema slice, and
        # RetrievalAgents their own public.*-only scoped slice, instead.
        schema_doc = schema_doc or DOWSER_SCHEMA_PATH.read_text()
        self.result_limit = result_limit
        # Gemini reliably returns SQLGenerationResult via pydantic_ai's default
        # tool-call output mode. Qwen2.5-Coder/Arctic-Text2SQL-R1 (served
        # locally via Ollama) do not — they tend to emit extra prose/SQL
        # alongside the tool call, breaking JSON parsing — but handle
        # NativeOutput's response_format json_schema mode correctly, so
        # non-Gemini models should pass use_native_output=True.
        output_type = NativeOutput(SQLGenerationResult) if use_native_output else SQLGenerationResult
        limit = f"- End with LIMIT {self.result_limit}." if self.result_limit is not None else ""
        self._agent = Agent(
            model=model or get_model(),
            output_type=output_type,
            system_prompt=SQL_GENERATION_SP.format(schema=schema_doc, limit=limit),
        )
        # run_agent applies Gemini's free-tier rate-limit throttle/retry — not
        # applicable to a locally-served model, so callers using a non-Gemini
        # model should pass plain_runner (or their own).
        self._runner: AgentRunner = runner or run_agent

    async def resolve(
        self, prompt: str, identity_hint: str | None = None, reveal: str | None = None
    ) -> NaiveSQLOutcome:
        """`reveal`, when given, is a pseudonymized @-mention alias (see
        pseudonymization.py) — reversed back to the real account name in the
        generated SQL text, on every attempt, before it's validated or
        executed, so the query actually matches real database rows and the
        returned NaiveSQLOutcome.sql reflects what really ran."""
        history: list[FailedAttempt] = []
        base_prompt = self._build_prompt(prompt, identity_hint)
        current_prompt = base_prompt
        total_usage = RunUsage()

        for attempt in range(1, self.MAX_ATTEMPTS + 1):
            result = await self._runner(self._agent, current_prompt)
            total_usage += result.usage()
            # Aliased form — this is what any retry prompt below echoes back
            # to the model, so a retry never re-exposes the real handle.
            sql_text = result.output.sql.strip()
            # Revealed form — only for execution and the outcome actually
            # reported/persisted, since the DB has real usernames, not
            # aliases.
            executable_sql = reveal_alias(sql_text, reveal) if reveal else sql_text

            error = self._guard(executable_sql)
            rows: list[dict] | None = None
            if error is None:
                try:
                    rows = await self._execute(executable_sql)
                except Exception as exc:
                    error = str(exc)

            if error is None and rows:
                return NaiveSQLOutcome(
                    sql=executable_sql,
                    rows=rows if self.result_limit is None else rows[: self.result_limit],
                    error=None,
                    attempts=attempt, history=history, usage=total_usage,
                )

            if error is None:
                # Executed fine, just empty — a legitimate outcome, but worth
                # one nudge to rule out an overly strict filter before we
                # accept it, unless this was the last attempt available.
                if attempt == self.MAX_ATTEMPTS:
                    return NaiveSQLOutcome(
                        sql=executable_sql, rows=[], error=None,
                        attempts=attempt, history=history, usage=total_usage,
                    )
                error = EMPTY_RESULT_MESSAGE

            if attempt == self.MAX_ATTEMPTS:
                return NaiveSQLOutcome(
                    sql=executable_sql, rows=None, error=error,
                    attempts=attempt, history=history, usage=total_usage,
                )

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
