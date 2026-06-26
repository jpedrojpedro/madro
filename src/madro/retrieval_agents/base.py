from abc import ABC, abstractmethod

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from django.conf import settings


class RetrievalAgent(ABC):
    """Base class for local retrieval agents that query a remote Postgres DB."""

    async def connect(self) -> AsyncConnection:
        return await AsyncConnection.connect(
            settings.RETRIEVAL_DB_URL, row_factory=dict_row
        )

    @abstractmethod
    async def run(self, job_id: str, demand: str, **kwargs) -> str | list | dict:
        """Execute the retrieval and return the raw result string."""
