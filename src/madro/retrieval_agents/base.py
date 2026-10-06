import importlib
from abc import ABC, abstractmethod

import httpx

from madro.retrieval_agents.identity import EntityRef
from madro.retrieval_agents.schema_docs import SCHEMA_DOCS
from madro.retrieval_agents.sql_resolvers import get_retrieval_sql_resolver


class RetrievalAgent(ABC):
    """Base class for local retrieval agents that query a remote Postgres DB
    (on the fly, via a per-model resolver — see
    retrieval_agents/sql_resolvers.py, _generate_and_execute(), and each
    concrete agent's run())."""

    # Declares which field in this agent's output records identifies its entities,
    # so EntityResolver can join records across agents without guessing from
    # field names. See identity.py.
    identity: EntityRef | None = None

    # Populated by _generate_and_execute() after each run() call — the
    # generated SQL and token usage, folded into this invocation's
    # provenance by AgentRunner.invoke() for observability (surfaces
    # automatically in the benchmark's per-job Allure attachment). Remote/
    # black-box agents (RemoteRetrievalAgent) have no equivalent and leave
    # this as None.
    last_provenance_extra: dict | None = None

    @abstractmethod
    async def run(self, job_id: str, demand: str, **kwargs) -> str | list | dict:
        """Execute the retrieval and return the raw result string."""

    @staticmethod
    def _with_username_hint(prompt: str, username: str | None) -> str:
        """Folds an explicit @username mention (extracted upstream by
        AgentRunner) into the natural-language prompt handed to the on-the-fly
        SQL generator, instead of a hardcoded mentioned_profile CTE — the LLM
        decides how best to use it (a WHERE filter, a ranking boost, ...)."""
        if not username:
            return prompt
        return f"{prompt}\n\nIf relevant, prioritize the profile with username '{username}'."

    _RECENCY_ROW_CAP = 5

    @classmethod
    def _with_recency_hint(cls, prompt: str, most_recent: bool) -> str:
        """Folds a relative-recency signal (extracted upstream by
        AgentRunner._extract_recency — "most recent", "last", "mais
        recentes") into the prompt. Ordering alone isn't enough: RelevanceRanker
        re-ranks every candidate by its own lexical/semantic RRF score, ignoring
        row order entirely, so an older-but-more-lexically-relevant row could
        still outrank the true most-recent one downstream. Explicitly capping
        the row count here keeps older rows out of the candidate pool
        altogether, so whatever RelevanceRanker does with what's left is still
        correct."""
        if not most_recent:
            return prompt
        return (
            f"{prompt}\n\nThis asks about the most recent item(s) — order by "
            f"published_at DESC and return at most {cls._RECENCY_ROW_CAP} rows, "
            f"so older (even if otherwise relevant) rows can't crowd these out."
        )

    def _resolve_identity(self, target_entity: str | None) -> EntityRef | None:
        """`target_entity` (DemandCategorizationAgent's declared answer entity
        for this sub-demand — see SubDemand.target_entity) overrides this
        agent's own static `identity` when they genuinely differ, e.g.
        PublicationFetcherAgent searching post content to characterize a
        restaurant *profile* rather than answer with the post itself. Falls
        back to `self.identity` as-is (preserving any `fallback` it declares)
        when there's no override to apply, rather than reconstructing an
        equivalent EntityRef from scratch."""
        if target_entity and (not self.identity or target_entity != self.identity.kind):
            return EntityRef(field=f"{target_entity}_id", kind=target_entity)
        return self.identity

    async def _generate_and_execute(
        self, prompt: str, tables: list[str], sample: int | None, schema_doc: str | None = None,
        target_entity: str | None = None, reveal: str | None = None,
    ) -> list[dict]:
        """Writes and runs this agent's scoped SQL on the fly (schema scoped
        to `tables`, public.* only — see retrieval_agents/schema_scope.py and
        schema_scope_ddl.py), via the configured per-model resolver (see
        sql_resolvers.py — AppConfig.retrieval_sql_backend picks Gemini or
        Arctic). Shared by every concrete local RetrievalAgent so the
        SQL-generation setup (model, schema scoping, identity aliasing,
        provenance capture) can't drift between them the way the old fixed
        SQL templates once did.

        `schema_doc`, when given, is a filename under retrieval_agents/schemas/
        (see each agent's own SCHEMA_DOC constant) resolved here to its text via
        schema_docs.SCHEMA_DOCS — GeminiSQLResolver's hand-authored scoped
        schema doc; ArcticSQLResolver ignores it and keeps building DDL from
        `tables`.

        `target_entity`, when it overrides this agent's own identity (see
        _resolve_identity), also changes `identity_hint` — the resolver tells
        the SQL generator to alias its result rows by the overridden field
        (e.g. `profile_id` instead of `publication_id`), not just changes
        what EntityResolver later calls the record's kind.

        `reveal`, when given, is a pseudonymized @-mention alias (see
        pseudonymization.py) this agent's own `username` kwarg carries —
        reversed back to the real account name inside the generated SQL
        text, before it executes, so the query actually matches real
        database rows."""
        schema_doc_text = SCHEMA_DOCS[schema_doc] if schema_doc else None
        resolver = get_retrieval_sql_resolver(tables, sample, schema_doc_text)
        identity = self._resolve_identity(target_entity)
        outcome = await resolver.resolve(
            prompt, identity_hint=identity.field if identity else None, reveal=reveal
        )
        self.last_provenance_extra = {
            "generated_sql": outcome.sql,
            "sql_attempts": outcome.attempts,
            # None whenever the final SQL executed (even if it returned no
            # rows) — how the benchmark tells a failed translation apart from
            # an empty one, since both return [] below.
            "sql_error": outcome.error,
            "usage": {
                "input_tokens": outcome.usage.input_tokens,
                "output_tokens": outcome.usage.output_tokens,
                "total_tokens": outcome.usage.total_tokens,
            },
            # Overrides AgentRunner.invoke()'s default `agent.identity` (the
            # catalog's static declaration) via dict spread — only present
            # when target_entity actually changed it.
            **({"identity": identity.model_dump()} if identity is not self.identity else {}),
        }
        return outcome.rows or []

    @classmethod
    def local_class_from_uri(cls, uri: str) -> type["RetrievalAgent"]:
        module_path = uri.removeprefix("local://").removesuffix(".py").replace("/", ".")
        module = importlib.import_module(module_path)
        for attr in vars(module).values():
            if isinstance(attr, type) and issubclass(attr, RetrievalAgent) and attr is not RetrievalAgent:
                return attr
        raise ValueError(f"No RetrievalAgent subclass found in {uri}")

    @classmethod
    def from_uri(cls, uri: str, timeout: float | None = None) -> "RetrievalAgent":
        if uri.startswith("local://"):
            return cls.local_class_from_uri(uri)()
        return RemoteRetrievalAgent(uri, timeout=timeout)


class RemoteRetrievalAgent(RetrievalAgent):
    """Invokes a retrieval agent hosted behind an HTTP endpoint."""

    def __init__(self, uri: str, timeout: float | None = None):
        self.uri = uri
        self.timeout = timeout

    async def run(self, job_id: str, demand: str, **kwargs) -> str | list | dict:
        payload = {"job_id": job_id, "demand": demand, **kwargs}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.uri, json=payload)
            response.raise_for_status()
            return response.json()
