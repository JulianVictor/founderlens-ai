"""Token verification in isolation from FastAPI."""

from __future__ import annotations

import datetime as dt

import jwt
import pytest

from app.core.exceptions import AuthenticationError
from app.core.security import SupabaseJWTVerifier
from tests.conftest import JWT_AUDIENCE, JWT_SECRET, OWNER_ID, TokenFactory


@pytest.fixture
def verifier() -> SupabaseJWTVerifier:
    return SupabaseJWTVerifier(secret=JWT_SECRET, audience=JWT_AUDIENCE)


def test_missing_secret_fails_at_construction() -> None:
    """A misconfigured deployment should not start and then reject everyone."""
    with pytest.raises(ValueError, match="SUPABASE_JWT_SECRET"):
        SupabaseJWTVerifier(secret="", audience=JWT_AUDIENCE)


def test_valid_token_yields_the_identity(
    verifier: SupabaseJWTVerifier, make_token: TokenFactory
) -> None:
    user = verifier.verify(make_token(OWNER_ID))

    assert user.id == OWNER_ID
    assert user.email == "ada@example.com"
    assert user.auth_role == "authenticated"


def test_claims_come_from_the_token_not_from_defaults(
    verifier: SupabaseJWTVerifier, make_token: TokenFactory
) -> None:
    user = verifier.verify(make_token(OWNER_ID, email="grace@example.com"))

    assert user.email == "grace@example.com"


def test_absent_email_is_none_rather_than_empty(
    verifier: SupabaseJWTVerifier, make_token: TokenFactory
) -> None:
    user = verifier.verify(make_token(OWNER_ID, email=None))

    assert user.email is None


def test_auth_role_defaults_when_the_claim_is_absent(
    verifier: SupabaseJWTVerifier, make_token: TokenFactory
) -> None:
    user = verifier.verify(make_token(OWNER_ID, role=None))

    assert user.auth_role == "authenticated"


@pytest.mark.parametrize(
    ("description", "kwargs"),
    [
        ("wrong signing secret", {"secret": "not-the-secret-padded-to-thirty-two-bytes"}),
        ("expired", {"expires_in": dt.timedelta(seconds=-1)}),
        ("no expiry claim", {"omit_exp": True}),
        ("different audience", {"audience": "another-service"}),
        ("no audience claim", {"audience": None}),
        ("subject is not a uuid", {"subject": "carol"}),
        ("empty subject", {"subject": ""}),
    ],
)
def test_rejected_tokens(
    verifier: SupabaseJWTVerifier,
    make_token: TokenFactory,
    description: str,
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(AuthenticationError):
        verifier.verify(make_token(OWNER_ID, **kwargs))


def test_unsigned_token_is_rejected(verifier: SupabaseJWTVerifier) -> None:
    """``alg: none`` must never be honoured, however well-formed the claims."""
    now = dt.datetime.now(tz=dt.UTC)
    token = jwt.encode(
        {
            "sub": str(OWNER_ID),
            "aud": JWT_AUDIENCE,
            "exp": now + dt.timedelta(hours=1),
        },
        key="",
        algorithm="none",
    )

    with pytest.raises(AuthenticationError):
        verifier.verify(token)


def test_not_a_jwt_at_all_is_rejected(verifier: SupabaseJWTVerifier) -> None:
    with pytest.raises(AuthenticationError):
        verifier.verify("clearly.not.a.jwt")


def test_empty_token_is_rejected(verifier: SupabaseJWTVerifier) -> None:
    with pytest.raises(AuthenticationError):
        verifier.verify("")


def test_leeway_admits_a_token_that_just_expired() -> None:
    """Small clock differences between signer and verifier are tolerated."""
    forgiving = SupabaseJWTVerifier(secret=JWT_SECRET, audience=JWT_AUDIENCE, leeway_seconds=120)
    now = dt.datetime.now(tz=dt.UTC)
    token = jwt.encode(
        {
            "sub": str(OWNER_ID),
            "aud": JWT_AUDIENCE,
            "exp": now - dt.timedelta(seconds=30),
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    assert forgiving.verify(token).id == OWNER_ID


def test_leeway_does_not_admit_a_long_expired_token() -> None:
    forgiving = SupabaseJWTVerifier(secret=JWT_SECRET, audience=JWT_AUDIENCE, leeway_seconds=120)
    now = dt.datetime.now(tz=dt.UTC)
    token = jwt.encode(
        {
            "sub": str(OWNER_ID),
            "aud": JWT_AUDIENCE,
            "exp": now - dt.timedelta(hours=1),
        },
        JWT_SECRET,
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError):
        forgiving.verify(token)
