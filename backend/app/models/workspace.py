"""Persistence shapes for workspaces and membership.

These mirror rows in Postgres. They are not API contracts -- see
:mod:`app.schemas.workspace` for those -- so that a column added here does not
silently become part of a public response.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class WorkspaceRole(StrEnum):
    """A member's role within one workspace.

    Mirrors the ``public.workspace_role`` enum in Postgres. The MVP has exactly
    two roles; more can be added, but each addition has to be placed in the
    ordering below deliberately rather than by accident of declaration order.
    """

    OWNER = "owner"
    MEMBER = "member"

    @property
    def rank(self) -> int:
        """Position in the privilege ordering. Higher grants strictly more."""
        return _ROLE_RANK[self]

    def satisfies(self, required: WorkspaceRole) -> bool:
        """Whether holding this role is enough to act at ``required``."""
        return self.rank >= required.rank


# Kept as an explicit table rather than relying on declaration order: reordering
# the enum members would otherwise silently change who can do what.
_ROLE_RANK: dict[WorkspaceRole, int] = {
    WorkspaceRole.MEMBER: 10,
    WorkspaceRole.OWNER: 20,
}


@dataclass(frozen=True, slots=True)
class Workspace:
    """A row of ``public.workspaces``."""

    id: UUID
    name: str
    created_by: UUID
    created_at: datetime


@dataclass(frozen=True, slots=True)
class WorkspaceMembership:
    """A row of ``public.workspace_members``, joined with its workspace.

    Carries the workspace itself because every caller that needs a membership
    also needs the workspace it refers to, and fetching them separately would
    mean two round trips to answer one authorization question.
    """

    workspace: Workspace
    user_id: UUID
    role: WorkspaceRole
    created_at: datetime
