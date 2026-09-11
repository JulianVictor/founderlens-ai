"""Workspace and membership persistence.

Every query here is scoped by ``user_id``. There is deliberately no
"get workspace by id" without a user: an unscoped read is exactly the shape of
query that leaks one workspace's data into another, so the repository does not
offer one. A caller who wants a workspace must say who is asking.

The SQL below is written out in full rather than assembled from shared
fragments. Interpolating any part of a query -- even a constant column list --
makes it harder to see at a glance that nothing user-supplied reaches the
statement text. Both statements pass every value as a bound parameter.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg

from app.models.workspace import Workspace, WorkspaceMembership, WorkspaceRole

_LIST_MEMBERSHIPS = """
    select w.id         as workspace_id,
           w.name       as workspace_name,
           w.created_by as workspace_created_by,
           w.created_at as workspace_created_at,
           m.user_id    as user_id,
           m.role       as role,
           m.created_at as member_created_at
      from public.workspace_members m
      join public.workspaces w on w.id = m.workspace_id
     where m.user_id = $1
     order by m.created_at asc, w.id asc
"""

_GET_MEMBERSHIP = """
    select w.id         as workspace_id,
           w.name       as workspace_name,
           w.created_by as workspace_created_by,
           w.created_at as workspace_created_at,
           m.user_id    as user_id,
           m.role       as role,
           m.created_at as member_created_at
      from public.workspace_members m
      join public.workspaces w on w.id = m.workspace_id
     where m.workspace_id = $1
       and m.user_id = $2
"""


class WorkspaceRepository(Protocol):
    """Read access to workspaces, always from the perspective of one user."""

    async def list_memberships(self, user_id: UUID) -> list[WorkspaceMembership]:
        """Every workspace ``user_id`` belongs to, oldest membership first."""
        ...

    async def get_membership(self, workspace_id: UUID, user_id: UUID) -> WorkspaceMembership | None:
        """``user_id``'s membership of ``workspace_id``, or ``None`` if absent.

        ``None`` covers both "no such workspace" and "not a member". The
        distinction is not available here on purpose -- see
        :class:`app.core.exceptions.WorkspaceNotFoundError`.
        """
        ...


def _to_membership(record: asyncpg.Record) -> WorkspaceMembership:
    return WorkspaceMembership(
        workspace=Workspace(
            id=record["workspace_id"],
            name=record["workspace_name"],
            created_by=record["workspace_created_by"],
            created_at=record["workspace_created_at"],
        ),
        user_id=record["user_id"],
        role=WorkspaceRole(record["role"]),
        created_at=record["member_created_at"],
    )


class PostgresWorkspaceRepository:
    """:class:`WorkspaceRepository` backed by Supabase Postgres."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def list_memberships(self, user_id: UUID) -> list[WorkspaceMembership]:
        records = await self._pool.fetch(_LIST_MEMBERSHIPS, user_id)
        return [_to_membership(record) for record in records]

    async def get_membership(self, workspace_id: UUID, user_id: UUID) -> WorkspaceMembership | None:
        record = await self._pool.fetchrow(_GET_MEMBERSHIP, workspace_id, user_id)
        return None if record is None else _to_membership(record)
