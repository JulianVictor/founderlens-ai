"""Profile persistence."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg

from app.models.profile import Profile


class ProfileRepository(Protocol):
    """Read access to user profiles."""

    async def get(self, user_id: UUID) -> Profile | None:
        """The profile for ``user_id``, or ``None`` if there is no row."""
        ...


class PostgresProfileRepository:
    """:class:`ProfileRepository` backed by Supabase Postgres."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, user_id: UUID) -> Profile | None:
        record = await self._pool.fetchrow(
            """
            select id, display_name, created_at
              from public.profiles
             where id = $1
            """,
            user_id,
        )
        if record is None:
            return None
        return Profile(
            id=record["id"],
            display_name=record["display_name"],
            created_at=record["created_at"],
        )
