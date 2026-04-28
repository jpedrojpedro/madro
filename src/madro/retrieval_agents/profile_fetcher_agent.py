import json

from psycopg import sql

from madro.retrieval_agents.base import RetrievalAgent


class ProfileFetcherAgent(RetrievalAgent):

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
        )
        SELECT id,
               full_name,
               regexp_replace(biography, '[\r\n]+', ' ', 'g') as bio,
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
        return json.dumps(rows)
