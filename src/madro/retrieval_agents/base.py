import importlib
from abc import ABC, abstractmethod

import httpx
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from django.conf import settings

from madro.retrieval_agents.identity import EntityRef


class RetrievalAgent(ABC):
    """Base class for local retrieval agents that query a remote Postgres DB."""

    # Declares which field in this agent's output records identifies its entities,
    # so EntityResolver can join records across agents without guessing from
    # field names. See identity.py.
    identity: EntityRef | None = None

    async def connect(self) -> AsyncConnection:
        return await AsyncConnection.connect(
            settings.RETRIEVAL_DB_URL, row_factory=dict_row
        )

    @abstractmethod
    async def run(self, job_id: str, demand: str, **kwargs) -> str | list | dict:
        """Execute the retrieval and return the raw result string."""

    @classmethod
    def local_class_from_uri(cls, uri: str) -> type["RetrievalAgent"]:
        module_path = uri.removeprefix("local://").removesuffix(".py").replace("/", ".")
        module = importlib.import_module(module_path)
        for attr in vars(module).values():
            if isinstance(attr, type) and issubclass(attr, RetrievalAgent) and attr is not RetrievalAgent:
                return attr
        raise ValueError(f"No RetrievalAgent subclass found in {uri}")

    @classmethod
    def from_uri(cls, uri: str, timeout: float | None = None) -> "RetrievalAgent":
        if uri.startswith("local://"):
            return cls.local_class_from_uri(uri)()
        return RemoteRetrievalAgent(uri, timeout=timeout)


class RemoteRetrievalAgent(RetrievalAgent):
    """Invokes a retrieval agent hosted behind an HTTP endpoint."""

    def __init__(self, uri: str, timeout: float | None = None):
        self.uri = uri
        self.timeout = timeout

    async def run(self, job_id: str, demand: str, **kwargs) -> str | list | dict:
        payload = {"job_id": job_id, "demand": demand, **kwargs}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(self.uri, json=payload)
            response.raise_for_status()
            return response.json()
