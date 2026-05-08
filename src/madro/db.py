import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

import psycopg
from psycopg import AsyncConnection


def _conninfo() -> str:
    return (
        os.environ.get("DATABASE_URL", "postgresql://madro:madro@localhost:5432/madro")
        .replace("postgres://", "postgresql://", 1)
    )


@asynccontextmanager
async def async_cursor() -> AsyncIterator[psycopg.AsyncCursor]:
    async with await AsyncConnection.connect(_conninfo()) as conn:
        async with conn.cursor() as cur:
            yield cur
