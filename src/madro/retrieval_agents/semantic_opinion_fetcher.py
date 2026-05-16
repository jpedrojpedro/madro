import json

from madro.retrieval_agents.base import RetrievalAgent


class SemanticOpinionFetcherAgent(RetrievalAgent):

    async def run(self, job_id: str, demand: str, **kwargs) -> str:
        sample = kwargs.get("sample") or 10
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")

        params = []
        date_filter = ""
        if date_from:
            date_filter += " AND published_at >= %s"
            params.append(date_from)
        if date_to:
            date_filter += " AND published_at <= %s"
            params.append(date_to)
        params.append(sample)

        query = f"""
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
        """
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()
        return json.dumps(rows)
