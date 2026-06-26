import json

from psycopg import sql
from madro.retrieval_agents.base import RetrievalAgent
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional


class CommentSearchResult(BaseModel):
    comment_id: int = Field(description="The unique identifier of the comment.")
    publication_id: int = Field(description="The identifier of the publication this comment belongs to.")
    profile_id: int = Field(description="The identifier of the profile that authored the comment.")
    comment: Optional[str] = Field(default=None, description="The text content/annotation of the comment.")
    num_likes: int = Field(default=0, description="The total number of likes this comment has received.")
    published_at: datetime = Field(description="The timestamp when the comment was published.")


class SemanticOpinionFetcherAgent(RetrievalAgent):

    async def run(self, job_id: str, demand: str, **kwargs) -> list:
        sample = kwargs.get("sample") or 10
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")

        params = []
        date_clauses = []
        if date_from:
            date_clauses.append(sql.SQL("AND published_at >= %s"))
            params.append(date_from)
        if date_to:
            date_clauses.append(sql.SQL("AND published_at <= %s"))
            params.append(date_to)
        params.append(sample)

        query = sql.SQL("""
        SELECT id AS comment_id,
               publication_id,
               profile_id,
               annotation AS comment,
               num_likes,
               published_at
        FROM comment
        WHERE reply_to IS NULL
          {date_filter}
        ORDER BY published_at DESC, num_likes DESC
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
                profile_id=row["profile_id"],
                comment=row["comment"],
                num_likes=row["num_likes"],
                published_at=row["published_at"]
            ).model_dump(mode="json")
            for row in rows
        ]
