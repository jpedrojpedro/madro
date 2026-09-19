"""
Per-model resolver classes for RetrievalAgents' on-the-fly SQL generation —
each bundles the model, runner, output mode, and schema-doc builder that
have to be kept consistent together, instead of a caller assembling
NaiveSQLBaseline's constructor args by hand and risking a mismatched
combination (e.g. Gemini's throttled runner with Arctic's model, or a
Markdown schema doc with a model expecting DDL).

Which one RetrievalAgents actually use is a config toggle
(AppConfig.retrieval_sql_backend) — see get_retrieval_sql_resolver().
"""

from madro.config import get_model, get_retrieval_sql_model, load_config, run_agent
from madro.retrieval_agents.schema_scope import build_scoped_schema
from madro.retrieval_agents.schema_scope_ddl import build_scoped_schema_ddl
from madro.sql_generation import NaiveSQLBaseline, NaiveSQLOutcome, plain_runner


class GeminiSQLResolver:
    """Gemini, via each agent's hand-authored scoped schema doc (see
    retrieval_agents/schemas/*.md and schema_docs.py). Falls back to the
    dynamically-built annotated Markdown (configs/dowser_schema.md, via
    schema_scope.build_scoped_schema) when the caller has no static doc for
    this agent yet — see docs/adr/0007-static-per-agent-schema-docs-for-gemini-resolver.md."""

    def __init__(self, tables: list[str], result_limit: int | None, schema_doc: str | None = None):
        self._baseline = NaiveSQLBaseline(
            model=get_model(),
            runner=run_agent,
            schema_doc=schema_doc or build_scoped_schema(tables),
            result_limit=result_limit,
        )

    async def resolve(self, prompt: str, identity_hint: str | None = None) -> NaiveSQLOutcome:
        return await self._baseline.resolve(prompt, identity_hint=identity_hint)


class ArcticSQLResolver:
    """Arctic-Text2SQL-R1-7B, served locally via Ollama, via a pure-DDL schema
    description (configs/dowser_schema.sql) — Arctic was trained on literal
    `CREATE TABLE` statements, unlike Gemini's annotated Markdown doc.

    use_native_output=True: same reasoning as the naive-SQL baseline's
    Qwen/Llama setup (see NaiveSQLBaseline's use_native_output) — a smaller
    model tends to wrap its tool call in prose against a long, schema-laden
    prompt, breaking JSON parsing under pydantic_ai's default tool-call mode.
    Unverified for Arctic specifically (it hadn't been wired to any
    RetrievalAgent before this) — revisit once it's actually been exercised
    against real questions."""

    def __init__(self, tables: list[str], result_limit: int | None, schema_doc: str | None = None):
        # schema_doc is accepted only for a uniform resolver constructor
        # signature across backends (see get_retrieval_sql_resolver()) — Arctic
        # always builds its own DDL-scoped doc from `tables` and ignores it.
        self._baseline = NaiveSQLBaseline(
            model=get_retrieval_sql_model(),
            runner=plain_runner,
            use_native_output=True,
            schema_doc=build_scoped_schema_ddl(tables),
            result_limit=result_limit,
        )

    async def resolve(self, prompt: str, identity_hint: str | None = None) -> NaiveSQLOutcome:
        return await self._baseline.resolve(prompt, identity_hint=identity_hint)


_RESOLVERS = {"gemini": GeminiSQLResolver, "arctic": ArcticSQLResolver}


def get_retrieval_sql_resolver(tables: list[str], result_limit: int | None, schema_doc: str | None = None):
    """The configured resolver (AppConfig.retrieval_sql_backend, default
    "gemini") for one RetrievalAgent call, scoped to `tables` (Arctic) and/or
    `schema_doc` (Gemini's hand-authored doc, if the caller has one)."""
    cfg = load_config()
    try:
        resolver_cls = _RESOLVERS[cfg.retrieval_sql_backend]
    except KeyError:
        raise ValueError(
            f"Unknown retrieval_sql_backend {cfg.retrieval_sql_backend!r} — "
            f"expected one of {sorted(_RESOLVERS)}"
        )
    return resolver_cls(tables, result_limit, schema_doc)
