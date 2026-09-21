from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef

SCOPED_TABLES = ["publication", "profile", "publication_collab"]
SCHEMA_DOC = "publication_schema.md"


class PublicationFetcherAgent(RetrievalAgent):
    identity = EntityRef(field="publication_id", kind="publication")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample", 25)
        username = kwargs.get("username")
        target_entity = kwargs.get("target_entity")
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")
        most_recent = kwargs.get("most_recent", False)

        prompt = self._with_username_hint(demand, username)
        if date_from:
            prompt += f"\n\nOnly include publications published on or after {date_from}."
        if date_to:
            prompt += f"\n\nOnly include publications published on or before {date_to}."
        prompt = self._with_recency_hint(prompt, most_recent)
        return await self._generate_and_execute(
            prompt, SCOPED_TABLES, sample, schema_doc=SCHEMA_DOC, target_entity=target_entity
        )
