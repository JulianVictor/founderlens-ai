"""Workspace authorization logic, tested without HTTP in the way.

These assertions are about the decision itself -- who may act on what -- rather
than about the status code it eventually becomes.
"""

from __future__ import annotations

import pytest

from app.core.exceptions import InsufficientWorkspaceRoleError, WorkspaceNotFoundError
from app.models.workspace import WorkspaceRole
from app.services.workspace_service import WorkspaceService
from tests.conftest import (
    MEMBER_ID,
    OTHER_WORKSPACE_ID,
    OUTSIDER_ID,
    OWNER_ID,
    UNKNOWN_WORKSPACE_ID,
    WORKSPACE_ID,
    FakeWorkspaceRepository,
)


@pytest.fixture
def service(workspace_repository: FakeWorkspaceRepository) -> WorkspaceService:
    return WorkspaceService(workspace_repository)


class TestRoleOrdering:
    """The privilege ordering itself, which everything else is built on."""

    def test_owner_satisfies_member(self) -> None:
        assert WorkspaceRole.OWNER.satisfies(WorkspaceRole.MEMBER)

    def test_member_does_not_satisfy_owner(self) -> None:
        assert not WorkspaceRole.MEMBER.satisfies(WorkspaceRole.OWNER)

    @pytest.mark.parametrize("role", list(WorkspaceRole))
    def test_every_role_satisfies_itself(self, role: WorkspaceRole) -> None:
        assert role.satisfies(role)

    def test_every_role_has_a_rank(self) -> None:
        """A role added to the enum without a rank would fail here rather than
        raising KeyError in the middle of an authorization check."""
        for role in WorkspaceRole:
            assert isinstance(role.rank, int)

    def test_ranks_are_distinct(self) -> None:
        ranks = [role.rank for role in WorkspaceRole]
        assert len(ranks) == len(set(ranks))


class TestListForUser:
    async def test_returns_only_the_users_memberships(self, service: WorkspaceService) -> None:
        result = await service.list_for_user(OWNER_ID)

        assert [m.workspace.id for m in result] == [WORKSPACE_ID]

    async def test_user_with_no_memberships_gets_an_empty_list(
        self, service: WorkspaceService
    ) -> None:
        from uuid import uuid4

        assert await service.list_for_user(uuid4()) == []


class TestRequireMembership:
    async def test_member_is_authorised(self, service: WorkspaceService) -> None:
        membership = await service.require_membership(WORKSPACE_ID, MEMBER_ID)

        assert membership.role is WorkspaceRole.MEMBER
        assert membership.workspace.id == WORKSPACE_ID

    async def test_owner_is_authorised(self, service: WorkspaceService) -> None:
        membership = await service.require_membership(WORKSPACE_ID, OWNER_ID)

        assert membership.role is WorkspaceRole.OWNER

    async def test_non_member_is_refused(self, service: WorkspaceService) -> None:
        with pytest.raises(WorkspaceNotFoundError):
            await service.require_membership(OTHER_WORKSPACE_ID, OWNER_ID)

    async def test_unknown_workspace_raises_the_same_error_as_non_membership(
        self, service: WorkspaceService
    ) -> None:
        """Both must be the same exception type, or the HTTP layer cannot help
        but distinguish them."""
        with pytest.raises(WorkspaceNotFoundError):
            await service.require_membership(UNKNOWN_WORKSPACE_ID, OWNER_ID)

    async def test_membership_does_not_leak_across_workspaces(
        self, service: WorkspaceService
    ) -> None:
        """Being an owner somewhere grants nothing anywhere else."""
        with pytest.raises(WorkspaceNotFoundError):
            await service.require_membership(WORKSPACE_ID, OUTSIDER_ID)

    async def test_owner_satisfies_an_owner_requirement(self, service: WorkspaceService) -> None:
        membership = await service.require_membership(
            WORKSPACE_ID, OWNER_ID, minimum_role=WorkspaceRole.OWNER
        )

        assert membership.role is WorkspaceRole.OWNER

    async def test_member_fails_an_owner_requirement(self, service: WorkspaceService) -> None:
        with pytest.raises(InsufficientWorkspaceRoleError) as caught:
            await service.require_membership(
                WORKSPACE_ID, MEMBER_ID, minimum_role=WorkspaceRole.OWNER
            )

        assert caught.value.required == "owner"
        assert caught.value.actual == "member"

    async def test_non_member_fails_closed_before_the_role_check(
        self, service: WorkspaceService
    ) -> None:
        """Ordering matters: raising InsufficientWorkspaceRole here would
        confirm the workspace exists to someone outside it."""
        with pytest.raises(WorkspaceNotFoundError):
            await service.require_membership(
                OTHER_WORKSPACE_ID, OWNER_ID, minimum_role=WorkspaceRole.OWNER
            )

    async def test_default_minimum_role_is_member(self, service: WorkspaceService) -> None:
        """A caller who forgets to pass minimum_role gets the permissive-but-
        still-authenticated default, not an unchecked pass."""
        membership = await service.require_membership(WORKSPACE_ID, MEMBER_ID)

        assert membership.role is WorkspaceRole.MEMBER
