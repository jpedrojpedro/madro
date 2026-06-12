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

    async def run(self, job_id: str, demand: str, **kwargs) -> str:
        sample = kwargs.get("sample") or 10
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")

        params = []
        if date_from or date_to:
            clauses = []
            if date_from:
                clauses.append(sql.SQL("AND published_at >= %s"))
            if date_to:
                clauses.append(sql.SQL("AND published_at <= %s"))
            date_filter = sql.SQL(" ").join(clauses)
        else:
            date_filter = sql.SQL("")
        params.append(date_filter)
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
          {}
        ORDER BY published_at DESC, num_likes DESC
        LIMIT %s
        """)
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()

        results = [
            CommentSearchResult(
                comment_id=row["comment_id"],
                publication_id=row["publication_id"],
                profile_id=row["profile_id"],
                comment=row["comment"],
                num_likes=row["num_likes"],
                published_at=row["published_at"]
            )
            for row in rows
        ]

        return json.dumps(results, ident=2)
