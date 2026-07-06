"""Async MySQL access layer backed by an aiomysql connection pool.

Feature cogs create their own tables (prefixed ``vibe_``) in their ``cog_load``
methods, so the schema lives next to the code that uses it.
"""

from __future__ import annotations

import os
from typing import Any, Optional, Sequence

import aiomysql


class Database:
    """Thin async wrapper around an aiomysql connection pool."""

    def __init__(self) -> None:
        self._pool: Optional[aiomysql.Pool] = None

    async def connect(self) -> None:
        """Create the connection pool from the MYSQL_* environment variables."""
        self._pool = await aiomysql.create_pool(
            host=os.getenv("MYSQL_HOST", "localhost"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER"),
            password=os.getenv("MYSQL_PASSWORD"),
            db=os.getenv("MYSQL_DATABASE"),
            charset="utf8mb4",
            autocommit=True,
            minsize=1,
            maxsize=5,
            pool_recycle=1800,
        )

    async def close(self) -> None:
        """Close the pool and wait for open connections to finish."""
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def execute(self, query: str, params: Sequence[Any] = ()) -> int:
        """Run a write query and return the last insert id or affected rows."""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params)
                return cur.lastrowid or cur.rowcount

    async def fetchone(
        self, query: str, params: Sequence[Any] = ()
    ) -> Optional[dict[str, Any]]:
        """Return the first matching row as a dict, or None."""
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, params)
                return await cur.fetchone()

    async def fetchall(
        self, query: str, params: Sequence[Any] = ()
    ) -> list[dict[str, Any]]:
        """Return all matching rows as a list of dicts."""
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(query, params)
                return list(await cur.fetchall())
