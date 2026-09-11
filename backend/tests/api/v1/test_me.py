"""GET /api/v1/me."""

from __future__ import annotations

from collections.abc import Callable

from httpx import AsyncClient

from tests.conftest import MEMBER_ID, OWNER_ID

Headers = Callable[..., dict[str, str]]


async def test_returns_the_callers_identity(client: AsyncClient, auth_headers: Headers) -> None:
    response = await client.get("/api/v1/me", headers=auth_headers(OWNER_ID))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(OWNER_ID)
    assert body["display_name"] == "Ada"


async def test_identity_follows_the_token_not_the_request(
    client: AsyncClient, auth_headers: Headers
) -> None:
    """Two tokens, two identities, from the same endpoint taking no input."""
    ada = await client.get("/api/v1/me", headers=auth_headers(OWNER_ID))
    grace = await client.get("/api/v1/me", headers=auth_headers(MEMBER_ID))

    assert ada.json()["display_name"] == "Ada"
    assert grace.json()["display_name"] == "Grace"


async def test_user_without_a_profile_is_a_server_error(
    client: AsyncClient, auth_headers: Headers
) -> None:
    """The signup trigger guarantees a profile. Its absence is a broken
    deployment, and reporting 200 with a blank name would hide that."""
    from uuid import UUID

    stranger = UUID("00000000-0000-4000-8000-0000000000bb")

    response = await client.get("/api/v1/me", headers=auth_headers(stranger))

    assert response.status_code == 500
