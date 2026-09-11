"""Workspaces visible to the authenticated caller."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import CurrentUser, WorkspaceServiceDep
from app.schemas.workspace import WorkspaceListResponse, WorkspaceResponse

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


@router.get(
    "",
    response_model=WorkspaceListResponse,
    summary="Workspaces the calling user belongs to",
)
async def list_workspaces(
    user: CurrentUser, workspaces: WorkspaceServiceDep
) -> WorkspaceListResponse:
    """List the caller's workspaces, with their role in each.

    Scoped by the authenticated user id, never by a client-supplied one. There
    is no way to ask this endpoint about somebody else's workspaces, because it
    never accepts a user id as input.
    """
    memberships = await workspaces.list_for_user(user.id)
    return WorkspaceListResponse(
        workspaces=[WorkspaceResponse.from_membership(m) for m in memberships]
    )
