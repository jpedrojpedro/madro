import json

from psycopg import sql
from pydantic import BaseModel, Field
from typing import Optional
from madro.retrieval_agents.base import RetrievalAgent


class ProfileFetcherResult(BaseModel):
    profile_id: int = Field(description="The unique identifier of the user profile.")
    profile_full_name: str = Field(description="The complete name of the profile owner.")
    profile_bio: Optional[str] = Field(default=None, description="The profile biography description.")
    rnk: float = Field(description="Postgres ts_rank full-text search score – between 0 and 1.")


class ProfileFetcherAgent(RetrievalAgent):

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") if kwargs.get("sample") else 10
        query = sql.SQL("""
        WITH search_setup AS (
            SELECT to_tsquery(
                'english',
                array_to_string(
                    tsvector_to_array(
                        to_tsvector('english', %s)
                    ),
                    ' | '
                )
          ) AS query
        )
        SELECT id as profile_id,
               full_name as profile_full_name,
               regexp_replace(biography, '[\r\n]+', ' ', 'g') as profile_bio,
               ts_rank(biography_lexemes, query, 32) as rnk
        FROM public.profile, search_setup
        WHERE biography_lexemes @@ query
        ORDER BY rnk DESC
        LIMIT %s
        """)
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (demand, sample))
                rows = await cur.fetchall()

        return [
            ProfileFetcherResult(
                profile_id=row["profile_id"],
                profile_full_name=row["profile_full_name"],
                profile_bio=row["profile_bio"],
                rnk=row["rnk"],
            ).model_dump(mode="json")
            for row in rows
        ]
