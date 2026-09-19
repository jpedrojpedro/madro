from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef

SCOPED_TABLES = ["profile"]
SCHEMA_DOC = "profile_schema.md"


class ProfileFetcherAgent(RetrievalAgent):
    identity = EntityRef(field="profile_id", kind="profile")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample", 25)
        username = kwargs.get("username")

        prompt = self._with_username_hint(demand, username)
        return await self._generate_and_execute(prompt, SCOPED_TABLES, sample, schema_doc=SCHEMA_DOC)
