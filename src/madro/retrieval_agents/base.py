from abc import ABC, abstractmethod

import psycopg
from django.conf import settings


class RetrievalAgent(ABC):
    """Base class for local retrieval agents that query a remote Postgres DB."""

    async def connect(self) -> psycopg.AsyncConnection:
        return await psycopg.AsyncConnection.connect(settings.RETRIEVAL_DB_URL)

    @abstractmethod
    async def run(self, job_id: str, demand: str, schema: dict) -> str:
        """Execute the retrieval and return the raw result string."""
