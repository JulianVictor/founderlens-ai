"""Unauthenticated and badly-authenticated access to protected routes.

The property under test: a request that is not backed by a valid Supabase
access token never reaches application data, whatever it presents.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient

from tests.conftest import JWT_SECRET, OWNER_ID, TokenFactory

# Every route that must refuse an anonymous caller. New protected routes belong
# in this list; the parametrised tests below then cover them automatically.
PROTECTED_ROUTES = [
    "/api/v1/me",
    "/api/v1/workspaces",
    "/probe/workspaces/00000000-0000-4000-8000-0000000000a1",
]


@pytest.mark.parametrize("path", PROTECTED_ROUTES)
async def test_no_credentials_is_401(client: AsyncClient, path: str) -> None:
    response = await client.get(path)

    assert response.status_code == 401
    # 401 rather than 403: the caller has not failed an authorization check,
    # they have not identified themselves at all.
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("path", PROTECTED_ROUTES)
async def test_garbage_token_is_401(client: AsyncClient, path: str) -> None:
    response = await client.get(path, headers={"Authorization": "Bearer not-a-jwt"})

    assert response.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        "",
        "Bearer",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "token abc.def.ghi",
    ],
)
async def test_malformed_authorization_header_is_401(client: AsyncClient, header: str) -> None:
    response = await client.get("/api/v1/me", headers={"Authorization": header})

    assert response.status_code == 401


async def test_token_signed_with_wrong_secret_is_rejected(
    client: AsyncClient, make_token: TokenFactory
) -> None:
    """The signature is what makes a token trustworthy.

    This token is otherwise perfect -- right subject, right audience, not
    expired -- and must still be refused.
    """
    token = make_token(OWNER_ID, secret="an-attacker-chosen-secret-padded-to-32-bytes")

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_expired_token_is_rejected(client: AsyncClient, make_token: TokenFactory) -> None:
    token = make_token(OWNER_ID, expires_in=dt.timedelta(seconds=-60))

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_token_without_expiry_is_rejected(
    client: AsyncClient, make_token: TokenFactory
) -> None:
    """A token that never expires is not one we are willing to honour."""
    token = make_token(OWNER_ID, omit_exp=True)

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_token_for_another_audience_is_rejected(
    client: AsyncClient, make_token: TokenFactory
) -> None:
    """A token minted for a different service must not be accepted here."""
    token = make_token(OWNER_ID, audience="some-other-service")

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_unsigned_token_is_rejected(client: AsyncClient) -> None:
    """The ``alg: none`` attack: a token asking to be trusted without a signature."""
    import jwt

    now = dt.datetime.now(tz=dt.UTC)
    token = jwt.encode(
        {
            "sub": str(OWNER_ID),
            "aud": "authenticated",
            "exp": now + dt.timedelta(hours=1),
        },
        key="",
        algorithm="none",
    )

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_non_uuid_subject_is_rejected(client: AsyncClient, make_token: TokenFactory) -> None:
    """Correctly signed, but its subject is not a Supabase user id."""
    token = make_token(subject="' or 1=1 --")

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


async def test_valid_token_is_accepted(
    client: AsyncClient, auth_headers: Callable[..., dict[str, str]]
) -> None:
    """The control case: without this, the tests above could all pass vacuously."""
    response = await client.get("/api/v1/me", headers=auth_headers(OWNER_ID))

    assert response.status_code == 200
    assert response.json()["id"] == str(OWNER_ID)


async def test_error_body_does_not_explain_why_verification_failed(
    client: AsyncClient, make_token: TokenFactory
) -> None:
    """Rejections are indistinguishable to the caller.

    Telling an anonymous caller whether a token was expired, mis-signed or
    mis-audienced tells them which part of their forgery to fix next.
    """
    bodies = {
        client_response.json()["detail"]
        for client_response in [
            await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
            for token in (
                make_token(OWNER_ID, secret="a-wrong-secret-padded-to-thirty-two-bytes"),
                make_token(OWNER_ID, expires_in=dt.timedelta(seconds=-1)),
                make_token(OWNER_ID, audience="elsewhere"),
            )
        ]
    }

    assert len(bodies) == 1


async def test_secret_never_appears_in_a_response(
    client: AsyncClient, make_token: TokenFactory
) -> None:
    token = make_token(OWNER_ID, secret="a-wrong-secret-padded-to-thirty-two-bytes")

    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert JWT_SECRET not in response.text


def test_protected_routes_list_covers_every_v1_route(app: Any) -> None:
    """Guards the parametrised lists above against a new route slipping past.

    If a route is added under /api/v1 and not listed in PROTECTED_ROUTES, this
    fails -- so a new endpoint cannot quietly ship without an auth test.
    """
    # app.routes does not flatten included routers in this FastAPI version,
    # so the generated schema is the reliable list of what is actually exposed.
    v1_paths = {path for path in app.openapi()["paths"] if path.startswith("/api/v1")}
    covered = {path for path in PROTECTED_ROUTES if path.startswith("/api/v1")}

    assert v1_paths == covered
