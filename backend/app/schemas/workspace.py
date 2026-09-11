"""API contracts for workspaces."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.workspace import WorkspaceMembership, WorkspaceRole


class WorkspaceResponse(BaseModel):
    """One workspace, as seen by a member of it.

    ``role`` is the *caller's* role, not a property of the workspace. It is
    included because every client that lists workspaces immediately needs to
    know what the user may do in each one, and a second round trip per
    workspace to find out would be wasteful.
    """

    id: UUID
    name: str
    role: WorkspaceRole
    created_at: datetime

    @classmethod
    def from_membership(cls, membership: WorkspaceMembership) -> WorkspaceResponse:
        """Project a persistence-layer membership onto the API contract."""
        return cls(
            id=membership.workspace.id,
            name=membership.workspace.name,
            role=membership.role,
            created_at=membership.workspace.created_at,
        )


class WorkspaceListResponse(BaseModel):
    """Envelope for ``GET /api/v1/workspaces``.

    An object rather than a bare array, so that pagination or counts can be
    added later without breaking every existing client.
    """

    workspaces: list[WorkspaceResponse]
