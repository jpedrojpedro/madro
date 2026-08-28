from psycopg import sql
from pydantic import BaseModel, Field
from typing import Optional
from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef


class FollowerAnalysisResult(BaseModel):
    profile_id: int = Field(description="The unique identifier of the profile matched by the search demand (e.g. a restaurant).")
    profile_full_name: str = Field(description="The complete name of the matched profile.")
    profile_bio: Optional[str] = Field(default=None, description="The matched profile's biography description.")
    profile_num_followers: int = Field(default=0, description="The total number of followers the matched profile has.")
    follower_profile_id: int = Field(description="The unique identifier of the influencer profile following the matched profile.")
    follower_full_name: str = Field(description="The complete name of the influencer profile.")
    follower_num_followers: int = Field(default=0, description="The total number of followers the influencer profile has.")
    rnk: float = Field(description="Postgres ts_rank full-text search score – between 0 and 1.")


class FollowerAnalysisFetcherAgent(RetrievalAgent):
    """Finds profiles matching a search demand (via biography) that are followed
    by influencer-tier profiles, using the profile_relationship follow graph."""

    identity = EntityRef(field="profile_id", kind="profile")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") if kwargs.get("sample") else 25
        min_followers = kwargs.get("min_followers") if kwargs.get("min_followers") else 10000
        username = kwargs.get("username")

        # mentioned_profile resolves an explicit @username mention (if any) to
        # its profile id, so the matched (destination) profile is pinned to
        # that account and bumped to the top instead of relying solely on a
        # bio-text match.
        #
        # biography_lexemes is built with the custom `pt_en` text search
        # configuration (handles both English and Portuguese search terms
        # against this mostly-Portuguese content) — query with the same
        # configuration, never 'english'/'portuguese' alone, or matches are
        # silently missed.
        query = sql.SQL("""
        WITH mentioned_profile AS (
            SELECT id FROM public.profile WHERE username = %s
        ), search_setup AS (
            SELECT to_tsquery(
                'pt_en',
                array_to_string(
                    tsvector_to_array(
                        to_tsvector('pt_en', %s)
                    ),
                    ' | '
                )
          ) AS query
        )
        SELECT p.id as profile_id,
               p.full_name as profile_full_name,
               regexp_replace(p.biography, '[\r\n]+', ' ', 'g') as profile_bio,
               p.num_followers as profile_num_followers,
               f.id as follower_profile_id,
               f.full_name as follower_full_name,
               coalesce(f.num_followers, 0) as follower_num_followers,
               ts_rank(p.biography_lexemes, query, 32) as rnk
        FROM public.profile_relationship pr
        JOIN public.profile p ON p.id = pr.destination_profile_id
        JOIN public.profile f ON f.id = pr.origin_profile_id
        CROSS JOIN search_setup
        WHERE pr.edge = 'follows'
          AND f.num_followers >= %s
          AND (p.biography_lexemes @@ query OR p.id = (SELECT id FROM mentioned_profile))
        ORDER BY (p.id = (SELECT id FROM mentioned_profile)) DESC, rnk DESC, f.num_followers DESC
        LIMIT %s
        """)
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (username, demand, min_followers, sample))
                rows = await cur.fetchall()

        return [
            FollowerAnalysisResult(
                profile_id=row["profile_id"],
                profile_full_name=row["profile_full_name"],
                profile_bio=row["profile_bio"],
                profile_num_followers=row["profile_num_followers"],
                follower_profile_id=row["follower_profile_id"],
                follower_full_name=row["follower_full_name"],
                follower_num_followers=row["follower_num_followers"],
                rnk=row["rnk"],
            ).model_dump(mode="json")
            for row in rows
        ]
