import json

from psycopg import sql
from pydantic import BaseModel, Field
from datetime import datetime
from madro.retrieval_agents.base import RetrievalAgent


class ImageFetcherResult(BaseModel):
    publication_id: str = Field(description="The unique identifier of the publication.")
    position: int = Field(description="The structural sequence position.")
    extension: str = Field(description="The file type extension – jpg.")
    data: str = Field(description="The extracted raw file in b64 format.")
    published_at: datetime = Field(description="The publication timestamp.")
    rnk: float = Field(description="Postgres ts_rank full-text search score – between 0 and 1.")


class ImageFetcherAgent(RetrievalAgent):

    async def run(self, job_id: str, demand: str, **kwargs) -> str:
        sample = kwargs.get("sample") or 10
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")

        params = [demand, ['jpg']]
        date_filter = ""
        if date_from:
            date_filter += " AND p.published_at >= %s"
            params.append(date_from)
        if date_to:
            date_filter += " AND p.published_at <= %s"
            params.append(date_to)
        params.append(sample)

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
        SELECT rf.publication_id,
               rf.position,
               rf.extension,
               rf.data,
               p.published_at,
               ts_rank(p.description_lexemes, query, 32) as rnk
        FROM public.raw_file rf
        CROSS JOIN search_setup ss
        JOIN public.publication p ON rf.publication_id = p.id
        WHERE rf.data IS NOT NULL
          AND rf.extension = ANY(%s::file_extension[])
          AND p.description_lexemes @@ ss.query
          {date_filter}
        ORDER BY rnk DESC, p.published_at DESC, rf.position ASC
        LIMIT %s
        """)
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()

        results = [
            ImageFetcherResult(
                publication_id=row["publication_id"],
                position=row["position"],
                extension=row["extension"],
                data=(
                    bytes(row["data"]).decode()
                    if isinstance(row.get("data"), (bytes, memoryview))
                    else row["data"]
                ),
                published_at=datetime.strptime(
                    row["published_at"], "%Y-%m-%d %H:%M:%S"
                ),
                rnk=row["rnk"],
            ).model_dump(mode="json")
            for row in rows
        ]

        return json.dumps(results, ident=2)
