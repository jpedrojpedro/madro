from psycopg import sql
from pydantic import BaseModel, Field
from typing import Optional
from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef


class ProfileFetcherResult(BaseModel):
    profile_id: int = Field(description="The unique identifier of the user profile.")
    profile_full_name: str = Field(description="The complete name of the profile owner.")
    profile_bio: Optional[str] = Field(default=None, description="The profile biography description.")
    profile_num_medias: int = Field(default=0, description="The total number of publications shared by this profile.")
    profile_num_followers: int = Field(default=0, description="The total number of followers this profile has.")
    rnk: float = Field(description="Postgres ts_rank full-text search score – between 0 and 1.")


class ProfileFetcherAgent(RetrievalAgent):
    identity = EntityRef(field="profile_id", kind="profile")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") if kwargs.get("sample") else 25
        username = kwargs.get("username")

        # mentioned_profile resolves an explicit @username mention (if any) to
        # its profile id — an empty CTE (no mention) makes every comparison
        # against it NULL, so the lexical search below is unaffected; when it
        # does resolve, that profile is folded into the results and bumped to
        # the top instead of relying solely on a bio-text match.
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
               coalesce(p.num_medias, 0) as profile_num_medias,
               coalesce(p.num_followers, 0) as profile_num_followers,
               ts_rank(biography_lexemes, query, 32) as rnk
        FROM public.profile p, search_setup
        WHERE biography_lexemes @@ query
           OR p.id = (SELECT id FROM mentioned_profile)
        ORDER BY (p.id = (SELECT id FROM mentioned_profile)) DESC, rnk DESC
        LIMIT %s
        """)
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (username, demand, sample))
                rows = await cur.fetchall()

        return [
            ProfileFetcherResult(
                profile_id=row["profile_id"],
                profile_full_name=row["profile_full_name"],
                profile_bio=row["profile_bio"],
                profile_num_medias=row["profile_num_medias"],
                profile_num_followers=row["profile_num_followers"],
                rnk=row["rnk"],
            ).model_dump(mode="json")
            for row in rows
        ]
