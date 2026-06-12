import json

from psycopg import sql
from pydantic import BaseModel, Field
from typing import Optional
from madro.retrieval_agents.base import RetrievalAgent


class PublicationSearchResult(BaseModel):
    publication_id: int = Field(description="The unique identifier of the publication.")
    profile_id: int = Field(description="The identifier of the profile associated with the publication (either the collaborator or the primary author).")
    profile_full_name: str = Field(description="The name of the profile owner (resolved from either the collaborator or primary author profile).")
    publication_caption: Optional[str] = Field(default=None,description="The publication description text.")
    rnk: float = Field(description="Postgres ts_rank full-text search score – between 0 and 1.")


class PublicationFetcherAgent(RetrievalAgent):

    async def run(self, job_id: str, demand: str, **kwargs) -> str:
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
        ), publication_results AS (
            SELECT p.id,
                   p.profile_id,
                  regexp_replace(p.description, '[\r\n]+', ' ', 'g') as desc_,
                  ts_rank(p.description_lexemes, query, 32) as rnk
           FROM public.publication p,
                search_setup
           WHERE p.description_lexemes @@ query
            ORDER BY rnk DESC
            LIMIT 50
        )
        SELECT
            pr.id as publication_id,
            coalesce(pc.profile_id, pr.profile_id) as profile_id,
            coalesce(pf.full_name, pf2.full_name) as full_name,
            pr.desc_ as publication_caption,
            pr.rnk
        FROM publication_results pr
        LEFT JOIN public.publication_collab pc ON pr.id = pc.publication_id
        LEFT JOIN public.profile pf ON pc.profile_id = pf.id
        LEFT JOIN public.profile pf2 ON pr.profile_id = pf2.id
        ORDER BY rnk DESC
        LIMIT %s
        """)
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (demand, sample))
                rows = await cur.fetchall()

        results = [
            PublicationSearchResult(
                publication_id=row["publication_id"],
                profile_id=row["profile_id"],
                profile_full_name=row["full_name"],
                publication_caption=row["publication_caption"],
                rnk=row["rnk"]
            )
            for row in rows
        ]

        return json.dumps(results, ident=2)
