"""Postgres connection pool lifecycle.

The pool is created on application startup and closed on shutdown, so a
misconfigured or unreachable database fails loudly at boot rather than on the
first request that happens to need it.

The backend connects as a privileged role, which means row-level security does
*not* constrain it. RLS protects the browser, which talks to Supabase directly
with the user's own token. On this path, isolation is enforced by the repository
and service layers -- every query is scoped by ``user_id``, and every workspace
access goes through a membership check. The two mechanisms are independent, and
both are load-bearing.
"""

from __future__ import annotations

import logging
from typing import Final

import asyncpg

from app.core.config import Settings

logger: Final = logging.getLogger(__name__)


class Database:
    """Owns the asyncpg pool for the process."""

    def __init__(self, settings: Settings) -> None:
        self._dsn = settings.database_url.get_secret_value()
        self._pool: asyncpg.Pool | None = None

    @property
    def pool(self) -> asyncpg.Pool:
        """The live pool.

        Raises:
            RuntimeError: if accessed before :meth:`connect`. This means the
                application's lifespan did not run, which is a wiring bug
                rather than a condition to recover from.
        """
        if self._pool is None:
            raise RuntimeError("Database pool has not been initialised")
        return self._pool

    async def connect(self) -> None:
        if self._pool is not None:
            return
        logger.info("Connecting database pool")
        self._pool = await asyncpg.create_pool(dsn=self._dsn, min_size=1, max_size=10)

    async def disconnect(self) -> None:
        if self._pool is None:
            return
        logger.info("Closing database pool")
        await self._pool.close()
        self._pool = None
