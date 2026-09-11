"""Workspace listing and workspace-scoped access through the HTTP layer."""

from __future__ import annotations

from collections.abc import Callable

from httpx import AsyncClient

from tests.conftest import (
    MEMBER_ID,
    OTHER_WORKSPACE_ID,
    OUTSIDER_ID,
    OWNER_ID,
    UNKNOWN_WORKSPACE_ID,
    WORKSPACE_ID,
)

Headers = Callable[..., dict[str, str]]


async def test_lists_only_the_callers_workspaces(
    client: AsyncClient, auth_headers: Headers
) -> None:
    response = await client.get("/api/v1/workspaces", headers=auth_headers(OWNER_ID))

    assert response.status_code == 200
    returned = [w["id"] for w in response.json()["workspaces"]]
    assert returned == [str(WORKSPACE_ID)]
    # The outsider's workspace exists in the repository and must not appear.
    assert str(OTHER_WORKSPACE_ID) not in returned


async def test_members_and_owners_see_the_same_workspace_with_different_roles(
    client: AsyncClient, auth_headers: Headers
) -> None:
    owner = await client.get("/api/v1/workspaces", headers=auth_headers(OWNER_ID))
    member = await client.get("/api/v1/workspaces", headers=auth_headers(MEMBER_ID))

    assert owner.json()["workspaces"][0]["id"] == str(WORKSPACE_ID)
    assert member.json()["workspaces"][0]["id"] == str(WORKSPACE_ID)
    assert owner.json()["workspaces"][0]["role"] == "owner"
    assert member.json()["workspaces"][0]["role"] == "member"


async def test_outsider_does_not_see_other_peoples_workspaces(
    client: AsyncClient, auth_headers: Headers
) -> None:
    response = await client.get("/api/v1/workspaces", headers=auth_headers(OUTSIDER_ID))

    ids = [w["id"] for w in response.json()["workspaces"]]
    assert ids == [str(OTHER_WORKSPACE_ID)]
    assert str(WORKSPACE_ID) not in ids


async def test_listing_cannot_be_redirected_at_another_user(
    client: AsyncClient, auth_headers: Headers
) -> None:
    """The endpoint takes no user id, so none can be smuggled in.

    Query parameters naming another user must change nothing: scope comes from
    the verified token and only from there.
    """
    response = await client.get(
        "/api/v1/workspaces",
        params={"user_id": str(OUTSIDER_ID)},
        headers=auth_headers(OWNER_ID),
    )

    ids = [w["id"] for w in response.json()["workspaces"]]
    assert ids == [str(WORKSPACE_ID)]


async def test_member_may_reach_a_workspace_scoped_route(
    client: AsyncClient, auth_headers: Headers
) -> None:
    response = await client.get(
        f"/probe/workspaces/{WORKSPACE_ID}", headers=auth_headers(MEMBER_ID)
    )

    assert response.status_code == 200
    assert response.json() == {"workspace": "Acme Research", "role": "member"}


async def test_non_member_gets_404_not_403(client: AsyncClient, auth_headers: Headers) -> None:
    """A workspace the caller does not belong to is indistinguishable from one
    that does not exist.

    403 would confirm the workspace exists to someone with no right to know it
    does, turning UUID guessing into an enumeration oracle.
    """
    forbidden = await client.get(
        f"/probe/workspaces/{OTHER_WORKSPACE_ID}", headers=auth_headers(OWNER_ID)
    )
    nonexistent = await client.get(
        f"/probe/workspaces/{UNKNOWN_WORKSPACE_ID}", headers=auth_headers(OWNER_ID)
    )

    assert forbidden.status_code == 404
    assert nonexistent.status_code == 404
    assert forbidden.json() == nonexistent.json()


async def test_owner_only_route_admits_the_owner(
    client: AsyncClient, auth_headers: Headers
) -> None:
    response = await client.get(
        f"/probe/workspaces/{WORKSPACE_ID}/owner-only", headers=auth_headers(OWNER_ID)
    )

    assert response.status_code == 200
    assert response.json()["role"] == "owner"


async def test_owner_only_route_refuses_a_plain_member_with_403(
    client: AsyncClient, auth_headers: Headers
) -> None:
    """A member already knows this workspace exists, so 403 leaks nothing and
    is the more useful answer."""
    response = await client.get(
        f"/probe/workspaces/{WORKSPACE_ID}/owner-only", headers=auth_headers(MEMBER_ID)
    )

    assert response.status_code == 403
    assert "owner" in response.json()["detail"]


async def test_owner_only_route_still_404s_for_a_non_member(
    client: AsyncClient, auth_headers: Headers
) -> None:
    """Membership is checked before role: a stranger gets 404, not 403.

    The other order would tell a non-member that the workspace exists.
    """
    response = await client.get(
        f"/probe/workspaces/{OTHER_WORKSPACE_ID}/owner-only",
        headers=auth_headers(OWNER_ID),
    )

    assert response.status_code == 404


async def test_malformed_workspace_id_is_422(client: AsyncClient, auth_headers: Headers) -> None:
    response = await client.get("/probe/workspaces/not-a-uuid", headers=auth_headers(OWNER_ID))

    assert response.status_code == 422
