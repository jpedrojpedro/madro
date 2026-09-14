from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef

SCOPED_TABLES = ["publication", "profile", "publication_collab"]


class PublicationFetcherAgent(RetrievalAgent):
    identity = EntityRef(field="publication_id", kind="publication")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample", 25)
        username = kwargs.get("username")

        prompt = self._with_username_hint(demand, username)
        return await self._generate_and_execute(prompt, SCOPED_TABLES, sample)
