from madro.config import get_retrieval_sql_model
from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef
from madro.retrieval_agents.schema_scope import build_scoped_schema
from madro.sql_generation import NaiveSQLBaseline, plain_runner

SCOPED_TABLES = ["profile", "profile_relationship"]


class FollowerAnalysisFetcherAgent(RetrievalAgent):
    """Finds profiles matching a search demand (via biography) that are followed
    by influencer-tier profiles, using the profile_relationship follow graph."""

    identity = EntityRef(field="profile_id", kind="profile")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") or 25
        min_followers = kwargs.get("min_followers") or 10000
        username = kwargs.get("username")

        prompt = self._with_username_hint(demand, username)
        prompt += (
            f"\n\nOnly include a matched profile if it is followed (via "
            f"profile_relationship, edge='follows') by at least one other "
            f"profile with more than {min_followers} followers."
        )

        resolver = NaiveSQLBaseline(
            model=get_retrieval_sql_model(),
            runner=plain_runner,
            use_native_output=True,
            schema_doc=build_scoped_schema(SCOPED_TABLES),
            result_limit=sample,
        )
        outcome = await resolver.resolve(prompt, identity_hint=self.identity.field)
        return outcome.rows or []
