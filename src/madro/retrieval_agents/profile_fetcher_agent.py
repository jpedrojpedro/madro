from madro.retrieval_agents.base import RetrievalAgent


class ProfileFetcherAgent(RetrievalAgent):

    async def run(self, job_id: str, demand: str, schema: dict) -> str:
        async with await self.connect() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT id, name, email FROM profiles LIMIT 10")
                rows = await cur.fetchall()
        return "\n".join(f"{r[0]}: {r[1]} <{r[2]}>" for r in rows)
