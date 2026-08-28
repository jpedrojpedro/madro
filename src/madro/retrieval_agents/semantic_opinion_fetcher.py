from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef

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
        return await self._generate_and_execute(prompt, SCOPED_TABLES, sample)
