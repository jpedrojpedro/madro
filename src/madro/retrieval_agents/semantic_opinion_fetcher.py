from madro.config import get_retrieval_sql_model
from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef
from madro.retrieval_agents.schema_scope import build_scoped_schema
from madro.sql_generation import NaiveSQLBaseline, plain_runner

SCOPED_TABLES = ["comment", "publication", "profile"]


class SemanticOpinionFetcherAgent(RetrievalAgent):
    # comment_id is deliberately not declared here — it's this record's own row
    # id, and nothing else in the system would ever join on it.
    identity = EntityRef(field="publication_id", kind="publication")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") or 25
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")
        username = kwargs.get("username")

        prompt = self._with_username_hint(demand, username)
        if date_from:
            prompt += f"\n\nOnly include comments published on or after {date_from}."
        if date_to:
            prompt += f"\n\nOnly include comments published on or before {date_to}."

        resolver = NaiveSQLBaseline(
            model=get_retrieval_sql_model(),
            runner=plain_runner,
            use_native_output=True,
            schema_doc=build_scoped_schema(SCOPED_TABLES),
            result_limit=sample,
        )
        outcome = await resolver.resolve(prompt, identity_hint=self.identity.field)
        return outcome.rows or []
