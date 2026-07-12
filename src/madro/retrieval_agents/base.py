from abc import ABC, abstractmethod

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from django.conf import settings

from madro.retrieval_agents.identity import EntityRef, Relationship


class RetrievalAgent(ABC):
    """Base class for local retrieval agents that query a remote Postgres DB."""

    # Declares which field(s) in this agent's output records identify entities
    # (or relationships between entities), so EntityResolver can join records
    # across agents without guessing from field names. See identity.py.
    identity: tuple[EntityRef | Relationship, ...] = ()

    async def connect(self) -> AsyncConnection:
        return await AsyncConnection.connect(
            settings.RETRIEVAL_DB_URL, row_factory=dict_row
        )

    @abstractmethod
    async def run(self, job_id: str, demand: str, **kwargs) -> str | list | dict:
        """Execute the retrieval and return the raw result string."""
