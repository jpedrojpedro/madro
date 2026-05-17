import base64
import json

from madro.retrieval_agents.base import RetrievalAgent


class ImageFetcherAgent(RetrievalAgent):

    async def run(self, job_id: str, demand: str, **kwargs) -> str:
        sample = kwargs.get("sample") or 10
        date_from = kwargs.get("date_from")
        date_to = kwargs.get("date_to")

        params = [['jpg', 'mp4']]
        date_filter = ""
        if date_from:
            date_filter += " AND p.published_at >= %s"
            params.append(date_from)
        if date_to:
            date_filter += " AND p.published_at <= %s"
            params.append(date_to)
        params.append(sample)

        query = f"""
        SELECT rf.publication_id,
               rf.position,
               rf.extension,
               rf.data,
               p.published_at
        FROM public.raw_file rf
        JOIN public.publication p ON rf.publication_id = p.id
        WHERE rf.data IS NOT NULL
          AND rf.extension = ANY(%s::file_extension[])
          {date_filter}
        ORDER BY p.published_at DESC, rf.position ASC
        LIMIT %s
        """
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                rows = await cur.fetchall()

        for row in rows:
            if isinstance(row.get("data"), (bytes, memoryview)):
                row["data"] = base64.b64encode(bytes(row["data"])).decode()

        return json.dumps(rows, default=str)
