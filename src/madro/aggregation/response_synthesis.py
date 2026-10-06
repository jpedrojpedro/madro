"""
Response Synthesis Agent.

Takes the ranked entities and produces a natural-language answer
for the end user via LLM.
"""

import json

from pydantic_ai import Agent

from madro.config import get_model, run_agent
from madro.aggregation.relevance_ranker import RelevanceRanker
from madro.internal_agents.system_prompts import ResponseSynthesisSP
from madro.pseudonymization import extract_mentions, pseudonymize, depseudonymize


class ResponseSynthesisAgent:
    def __init__(self, ranker: RelevanceRanker | None = None):
        self._ranker = ranker or RelevanceRanker()

    async def synthesize(self, thread_id: str, demand: str) -> str:
        """Run ranking and synthesize a natural-language response."""
        ranked = await self._ranker.rank(thread_id)

        # Grouped by sub-demand rather than one flat list, so the model can
        # explicitly connect entities across groups (e.g. a profile in one
        # group to the items it follows in another) instead of reasoning over
        # an undifferentiated pool. An
        # entity resolved from more than one sub-demand's artifacts appears in
        # each group it belongs to.
        groups: dict[str, list] = {}
        for e in ranked:
            entry = {"entity_id": e.entity_id, "score": round(e.s_relevance, 4), **e.entity_data}
            for sub_demand in e.sub_demands:
                groups.setdefault(sub_demand, []).append(entry)
        evidence = json.dumps(
            [{"sub_demand": sub_demand, "entities": entries} for sub_demand, entries in groups.items()],
            ensure_ascii=False,
        )

        # Retrieved entity data (e.g. a `username` field) comes straight from
        # the real DB, so it re-exposes any @-mentioned handle even though
        # thread_workflow.py already pseudonymized it going into decomposition
        # — evidence here has no `@` prefix at all (bare DB column values), so
        # it needs its own pass.
        handles = extract_mentions(demand)
        safe_demand = pseudonymize(demand, handles)
        safe_evidence = pseudonymize(evidence, handles)

        agent = Agent(
            model=get_model(),
            system_prompt=ResponseSynthesisSP.format(demand=safe_demand, evidence=safe_evidence),
        )

        result = await run_agent(agent, safe_demand)
        return depseudonymize(result.output, handles)
