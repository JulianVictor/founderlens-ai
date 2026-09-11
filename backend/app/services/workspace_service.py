"""Workspace access control.

This module is the single place where "may this user do this to this workspace"
is decided on the API path. Route handlers do not make that judgement, and
neither do repositories -- one asks, the other fetches.
"""

from __future__ import annotations

from uuid import UUID

from app.core.exceptions import InsufficientWorkspaceRoleError, WorkspaceNotFoundError
from app.models.workspace import WorkspaceMembership, WorkspaceRole
from app.repositories.workspace_repository import WorkspaceRepository


class WorkspaceService:
    """Reads workspaces, and authorises access to them."""

    def __init__(self, repository: WorkspaceRepository) -> None:
        self._repository = repository

    async def list_for_user(self, user_id: UUID) -> list[WorkspaceMembership]:
        """Every workspace ``user_id`` belongs to.

        There is no unfiltered listing. A user sees their memberships, which is
        the same thing as "the workspaces that exist" from their point of view.
        """
        return await self._repository.list_memberships(user_id)

    async def require_membership(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        minimum_role: WorkspaceRole = WorkspaceRole.MEMBER,
    ) -> WorkspaceMembership:
        """Authorise ``user_id`` to act on ``workspace_id`` at ``minimum_role``.

        Returns the membership, so a caller that needs the workspace or the
        caller's role does not have to fetch it again.

        Raises:
            WorkspaceNotFoundError: the workspace does not exist, *or* the user
                is not a member. Indistinguishable on purpose -- a 403 here
                would confirm the existence of a workspace to someone with no
                right to know it exists.
            InsufficientWorkspaceRoleError: the user is a member, but their role
                does not satisfy ``minimum_role``. A 403 is correct here: they
                already know the workspace exists.
        """
        membership = await self._repository.get_membership(workspace_id, user_id)
        if membership is None:
            raise WorkspaceNotFoundError(workspace_id)

        if not membership.role.satisfies(minimum_role):
            raise InsufficientWorkspaceRoleError(
                workspace_id,
                required=minimum_role.value,
                actual=membership.role.value,
            )

        return membership
