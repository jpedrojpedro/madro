from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef

SCOPED_TABLES = ["comment", "publication", "profile", "publication_collab"]
SCHEMA_DOC = "semantic_opinion_schema.md"


class SemanticOpinionFetcherAgent(RetrievalAgent):
    # publication_id is the primary identity — a comment merges into its
    # publication's entity when some other artifact in the thread already
    # produced one. When none did, EntityResolver falls back to comment_id
    # instead of forcing the comment into a publication entity nothing else
    # in the thread surfaced. See docs/adr/0005-conditional-collapse-for-comment-identity.md.
    identity = EntityRef(
        field="publication_id",
        kind="publication",
        fallback=EntityRef(field="comment_id", kind="comment"),
    )

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample", 25)
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")
        username = kwargs.get("username")
        target_entity = kwargs.get("target_entity")
        most_recent = kwargs.get("most_recent", False)

        prompt = self._with_username_hint(demand, username)
        if date_from:
            prompt += f"\n\nOnly include comments published on or after {date_from}."
        if date_to:
            prompt += f"\n\nOnly include comments published on or before {date_to}."
        prompt = self._with_recency_hint(prompt, most_recent)
        return await self._generate_and_execute(
            prompt, SCOPED_TABLES, sample, schema_doc=SCHEMA_DOC, target_entity=target_entity, reveal=username
        )
