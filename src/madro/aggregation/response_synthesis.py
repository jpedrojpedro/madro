"""
Response Synthesis Agent.

Takes the ranked entities and produces a natural-language answer
for the end user via LLM.
"""

import json

from pydantic_ai import Agent

from madro.config import get_model
from madro.aggregation.relevance_ranker import RelevanceRanker
from madro.internal_agents.system_prompts import ResponseSynthesisSP


class ResponseSynthesisAgent:
    def __init__(self, ranker: RelevanceRanker | None = None):
        self._ranker = ranker or RelevanceRanker()

    async def synthesize(self, thread_id: str, demand: str) -> str:
        """Run ranking and synthesize a natural-language response."""
        ranked = await self._ranker.rank(thread_id, demand)
        evidence = json.dumps(
            [
                {"entity_id": e.entity_id, "score": round(e.s_relevance, 4), **e.entity_data}
                for e in ranked
            ],
            ensure_ascii=False,
        )

        agent = Agent(
            model=get_model(),
            system_prompt=ResponseSynthesisSP.format(demand=demand, evidence=evidence),
        )

        result = await agent.run(demand)
        return result.output
