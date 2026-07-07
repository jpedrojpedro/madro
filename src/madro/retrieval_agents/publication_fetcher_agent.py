from psycopg import sql
from pydantic import BaseModel, Field
from typing import Optional
from madro.retrieval_agents.base import RetrievalAgent


class PublicationSearchResult(BaseModel):
    publication_id: int = Field(description="The unique identifier of the publication.")
    profile_id: int = Field(description="The identifier of the profile associated with the publication (either the collaborator or the primary author).")
    profile_full_name: str = Field(description="The name of the profile owner (resolved from either the collaborator or primary author profile).")
    publication_caption: Optional[str] = Field(default=None, description="The publication description text.")
    publication_num_likes: int = Field(default=0, description="The total number of likes this publication has received.")
    publication_num_comments: int = Field(default=0, description="The total number of comments this publication has received.")
    rnk: float = Field(description="Postgres ts_rank full-text search score – between 0 and 1.")


class PublicationFetcherAgent(RetrievalAgent):

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") if kwargs.get("sample") else 10
        username = kwargs.get("username")

        # mentioned_profile resolves an explicit @username mention (if any) to
        # its profile id, so that profile's publications are folded into the
        # lexical results and bumped to the top instead of relying solely on
        # the caption text matching the demand.
        query = sql.SQL("""
        WITH mentioned_profile AS (
            SELECT id FROM public.profile WHERE username = %s
        ), search_setup AS (
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
                   coalesce(p.num_likes, 0) as num_likes,
                   coalesce(p.num_comments, 0) as num_comments,
                   regexp_replace(p.description, '[\r\n]+', ' ', 'g') as desc_,
                   ts_rank(p.description_lexemes, query, 32) as rnk,
                   (p.profile_id = (SELECT id FROM mentioned_profile)) as is_mentioned
           FROM public.publication p,
                search_setup
           WHERE p.description_lexemes @@ query
              OR p.profile_id = (SELECT id FROM mentioned_profile)
            ORDER BY is_mentioned DESC, rnk DESC
            LIMIT 50
        )
        SELECT
            pr.id as publication_id,
            coalesce(pc.profile_id, pr.profile_id) as profile_id,
            coalesce(pf.full_name, pf2.full_name) as profile_full_name,
            pr.desc_ as publication_caption,
            pr.num_likes as publication_num_likes,
            pr.num_comments as publication_num_comments,
            pr.rnk
        FROM publication_results pr
        LEFT JOIN public.publication_collab pc ON pr.id = pc.publication_id
        LEFT JOIN public.profile pf ON pc.profile_id = pf.id
        LEFT JOIN public.profile pf2 ON pr.profile_id = pf2.id
        ORDER BY pr.is_mentioned DESC, pr.rnk DESC
        LIMIT %s
        """)
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, (username, demand, sample))
                rows = await cur.fetchall()

        return [
            PublicationSearchResult(
                publication_id=row["publication_id"],
                profile_id=row["profile_id"],
                profile_full_name=row["profile_full_name"],
                publication_caption=row["publication_caption"],
                publication_num_likes=row["publication_num_likes"],
                publication_num_comments=row["publication_num_comments"],
                rnk=row["rnk"]
            ).model_dump(mode="json")
            for row in rows
        ]
