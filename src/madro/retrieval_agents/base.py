import importlib
from abc import ABC, abstractmethod

import httpx

from madro.config import get_model, run_agent
from madro.retrieval_agents.identity import EntityRef
from madro.retrieval_agents.schema_scope import build_scoped_schema
from madro.sql_generation import NaiveSQLBaseline


class RetrievalAgent(ABC):
    """Base class for local retrieval agents that query a remote Postgres DB
    (on the fly, via madro.sql_generation.NaiveSQLBaseline — see
    _generate_and_execute() and each concrete agent's run())."""

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

    async def _generate_and_execute(self, prompt: str, tables: list[str], sample: int | None) -> list[dict]:
        """Writes and runs this agent's scoped SQL on the fly (schema scoped
        to `tables`, public.* only — see retrieval_agents/schema_scope.py),
        via the shared NaiveSQLBaseline engine. Shared by every concrete
        local RetrievalAgent so the SQL-generation setup (model, schema
        scoping, identity aliasing, provenance capture) can't drift between
        them the way the old fixed SQL templates once did."""
        resolver = NaiveSQLBaseline(
            model=get_model(),
            runner=run_agent,
            schema_doc=build_scoped_schema(tables),
            result_limit=sample,
        )
        identity_field = self.identity.field if self.identity else None
        outcome = await resolver.resolve(prompt, identity_hint=identity_field)
        self.last_provenance_extra = {
            "generated_sql": outcome.sql,
            "sql_attempts": outcome.attempts,
            "usage": {
                "input_tokens": outcome.usage.input_tokens,
                "output_tokens": outcome.usage.output_tokens,
                "total_tokens": outcome.usage.total_tokens,
            },
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
