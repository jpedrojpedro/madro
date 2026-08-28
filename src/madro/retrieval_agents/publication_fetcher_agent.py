from madro.config import get_retrieval_sql_model
from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef
from madro.retrieval_agents.schema_scope import build_scoped_schema
from madro.sql_generation import NaiveSQLBaseline, plain_runner

SCOPED_TABLES = ["publication", "profile", "publication_collab"]


class PublicationFetcherAgent(RetrievalAgent):
    identity = EntityRef(field="publication_id", kind="publication")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") or 25
        username = kwargs.get("username")

        prompt = self._with_username_hint(demand, username)
        resolver = NaiveSQLBaseline(
            model=get_retrieval_sql_model(),
            runner=plain_runner,
            use_native_output=True,
            schema_doc=build_scoped_schema(SCOPED_TABLES),
            result_limit=sample,
        )
        outcome = await resolver.resolve(prompt, identity_hint=self.identity.field)
        return outcome.rows or []
