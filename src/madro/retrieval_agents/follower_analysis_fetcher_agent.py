from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef

SCOPED_TABLES = ["profile", "profile_relationship"]


class FollowerAnalysisFetcherAgent(RetrievalAgent):
    """Finds profiles matching a search demand (via biography) that are followed
    by influencer-tier profiles, using the profile_relationship follow graph."""

    identity = EntityRef(field="profile_id", kind="profile")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample", 25)
        min_followers = kwargs.get("min_followers") or 10000
        username = kwargs.get("username")

        prompt = self._with_username_hint(demand, username)
        prompt += (
            f"\n\nOnly include a matched profile if it is followed (via "
            f"profile_relationship, edge='follows') by at least one other "
            f"profile with more than {min_followers} followers."
        )
        return await self._generate_and_execute(prompt, SCOPED_TABLES, sample)
