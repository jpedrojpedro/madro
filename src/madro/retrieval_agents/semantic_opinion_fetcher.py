from psycopg import sql
from madro.retrieval_agents.base import RetrievalAgent
from madro.retrieval_agents.identity import EntityRef
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class CommentSearchResult(BaseModel):
    comment_id: int = Field(description="The unique identifier of the comment.")
    publication_id: int = Field(description="The identifier of the publication this comment belongs to.")
    publication_caption: Optional[str] = Field(default=None, description="The publication description text this comment belongs to.")
    profile_id: int = Field(description="The identifier of the profile that authored the comment.")
    comment: Optional[str] = Field(default=None, description="The text content/annotation of the comment.")
    num_likes: int = Field(default=0, description="The total number of likes this comment has received.")
    published_at: datetime = Field(description="The timestamp when the comment was published.")


class SemanticOpinionFetcherAgent(RetrievalAgent):
    # comment_id is deliberately not declared here — it's this record's own row
    # id, and nothing else in the system would ever join on it.
    identity = EntityRef(field="publication_id", kind="publication")

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") or 25
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")
        username = kwargs.get("username")

        # mentioned_profile resolves an explicit @username mention (if any) to
        # its profile id, so comments on that profile's publications are
        # bumped to the top — this agent has no other content-relevance
        # filter today, it otherwise just returns the most recent comments.
        params = [username]
        date_clauses = []
        if date_from:
            date_clauses.append(sql.SQL("AND published_at >= %s"))
            params.append(date_from)
        if date_to:
            date_clauses.append(sql.SQL("AND published_at <= %s"))
            params.append(date_to)
        params.append(sample)

        query = sql.SQL("""
        WITH mentioned_profile AS (
            SELECT id FROM public.profile WHERE username = %s
        )
        SELECT c.id AS comment_id,
               c.publication_id,
               regexp_replace(p.description, '[\r\n]+', ' ', 'g') AS publication_caption,
               c.profile_id,
               c.annotation AS comment,
               coalesce(c.num_likes, 0) as num_likes,
               c.published_at
        FROM comment c
        INNER JOIN public.publication p on p.id = c.publication_id
        WHERE c.reply_to IS NULL
          {date_filter}
        ORDER BY (p.profile_id = (SELECT id FROM mentioned_profile)) DESC, c.published_at DESC, c.num_likes DESC
        LIMIT %s
        """).format(date_filter=sql.SQL(" ").join(date_clauses))
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()

        return [
            CommentSearchResult(
                comment_id=row["comment_id"],
                publication_id=row["publication_id"],
                publication_caption=row["publication_caption"],
                profile_id=row["profile_id"],
                comment=row["comment"],
                num_likes=row["num_likes"],
                published_at=row["published_at"]
            ).model_dump(mode="json")
            for row in rows
        ]
