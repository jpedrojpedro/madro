from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef

SCOPED_TABLES = ["profile", "profile_relationship"]
SCHEMA_DOC = "follower_analysis_schema.md"


class FollowerAnalysisFetcherAgent(RetrievalAgent):
    """Finds profiles matching a search demand (via biography) that are followed
    by influencer-tier profiles, using the profile_relationship follow graph."""

    identity = EntityRef(field="profile_id", kind="profile")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample", 25)
        username = kwargs.get("username")
        target_entity = kwargs.get("target_entity")

        prompt = self._with_username_hint(demand, username)
        prompt += (
            "\n\nOnly include a matched profile if it is followed (via "
            "profile_relationship, edge='follows') by at least one other profile "
            "meeting the follower-count threshold stated in the demand above."
        )
        return await self._generate_and_execute(
            prompt, SCOPED_TABLES, sample, schema_doc=SCHEMA_DOC, target_entity=target_entity, reveal=username
        )
