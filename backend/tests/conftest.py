"""Shared pytest fixtures.

Tests run against in-memory fakes rather than a live Postgres. The fakes
implement the repository ``Protocol``s structurally, so if a protocol and an
implementation drift apart, mypy says so. What is being tested here is the
authentication and authorization logic, which is ours; that ``select`` returns
rows is Postgres's job.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any
from uuid import UUID

import jwt
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_profile_repository, get_workspace_repository
from app.core.config import Environment, Settings, get_settings
from app.main import create_app
from app.models.profile import Profile
from app.models.workspace import Workspace, WorkspaceMembership, WorkspaceRole
from app.repositories.profile_repository import ProfileRepository
from app.repositories.workspace_repository import WorkspaceRepository
from tests import probes

JWT_SECRET = "test-jwt-secret-not-used-anywhere-real"
JWT_AUDIENCE = "authenticated"

# Fixed identities so that assertions can name them.
OWNER_ID = UUID("00000000-0000-4000-8000-000000000001")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000000002")
OUTSIDER_ID = UUID("00000000-0000-4000-8000-000000000003")

WORKSPACE_ID = UUID("00000000-0000-4000-8000-0000000000a1")
OTHER_WORKSPACE_ID = UUID("00000000-0000-4000-8000-0000000000a2")
UNKNOWN_WORKSPACE_ID = UUID("00000000-0000-4000-8000-0000000000ff")

_CREATED_AT = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


class FakeWorkspaceRepository:
    """In-memory :class:`WorkspaceRepository`."""

    def __init__(self, memberships: list[WorkspaceMembership]) -> None:
        self.memberships = memberships

    async def list_memberships(self, user_id: UUID) -> list[WorkspaceMembership]:
        return [m for m in self.memberships if m.user_id == user_id]

    async def get_membership(self, workspace_id: UUID, user_id: UUID) -> WorkspaceMembership | None:
        for m in self.memberships:
            if m.workspace.id == workspace_id and m.user_id == user_id:
                return m
        return None


class FakeProfileRepository:
    """In-memory :class:`ProfileRepository`."""

    def __init__(self, profiles: dict[UUID, Profile]) -> None:
        self.profiles = profiles

    async def get(self, user_id: UUID) -> Profile | None:
        return self.profiles.get(user_id)


@pytest.fixture
def settings() -> Iterator[Settings]:
    """Isolated settings that ignore any developer ``.env`` on disk."""
    get_settings.cache_clear()
    yield Settings(
        _env_file=None,
        environment=Environment.TEST,
        supabase_jwt_secret=JWT_SECRET,  # type: ignore[arg-type]
        supabase_jwt_audience=JWT_AUDIENCE,
        supabase_jwt_leeway_seconds=0,
    )
    get_settings.cache_clear()


@pytest.fixture
def workspaces() -> dict[UUID, Workspace]:
    return {
        WORKSPACE_ID: Workspace(
            id=WORKSPACE_ID,
            name="Acme Research",
            created_by=OWNER_ID,
            created_at=_CREATED_AT,
        ),
        OTHER_WORKSPACE_ID: Workspace(
            id=OTHER_WORKSPACE_ID,
            name="Someone Else's Workspace",
            created_by=OUTSIDER_ID,
            created_at=_CREATED_AT,
        ),
    }


@pytest.fixture
def memberships(workspaces: dict[UUID, Workspace]) -> list[WorkspaceMembership]:
    """OWNER owns WORKSPACE_ID, MEMBER is a plain member of it, OUTSIDER owns
    OTHER_WORKSPACE_ID and belongs to nothing else."""
    return [
        WorkspaceMembership(
            workspace=workspaces[WORKSPACE_ID],
            user_id=OWNER_ID,
            role=WorkspaceRole.OWNER,
            created_at=_CREATED_AT,
        ),
        WorkspaceMembership(
            workspace=workspaces[WORKSPACE_ID],
            user_id=MEMBER_ID,
            role=WorkspaceRole.MEMBER,
            created_at=_CREATED_AT,
        ),
        WorkspaceMembership(
            workspace=workspaces[OTHER_WORKSPACE_ID],
            user_id=OUTSIDER_ID,
            role=WorkspaceRole.OWNER,
            created_at=_CREATED_AT,
        ),
    ]


@pytest.fixture
def profiles() -> dict[UUID, Profile]:
    return {
        OWNER_ID: Profile(id=OWNER_ID, display_name="Ada", created_at=_CREATED_AT),
        MEMBER_ID: Profile(id=MEMBER_ID, display_name="Grace", created_at=_CREATED_AT),
        OUTSIDER_ID: Profile(id=OUTSIDER_ID, display_name="Mallory", created_at=_CREATED_AT),
    }


@pytest.fixture
def workspace_repository(
    memberships: list[WorkspaceMembership],
) -> FakeWorkspaceRepository:
    return FakeWorkspaceRepository(memberships)


@pytest.fixture
def profile_repository(profiles: dict[UUID, Profile]) -> FakeProfileRepository:
    return FakeProfileRepository(profiles)


@pytest.fixture
def app(
    settings: Settings,
    workspace_repository: FakeWorkspaceRepository,
    profile_repository: FakeProfileRepository,
) -> FastAPI:
    """Application wired to the in-memory repositories.

    Overriding at the repository boundary rather than the service boundary keeps
    the real services -- and therefore the real authorization logic -- in the
    path under test.
    """
    application = create_app(settings)
    application.dependency_overrides[get_settings] = lambda: settings
    application.dependency_overrides[get_workspace_repository] = lambda: workspace_repository
    application.dependency_overrides[get_profile_repository] = lambda: profile_repository
    application.include_router(probes.router)
    return application


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Unauthenticated HTTP client bound directly to the ASGI app."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as async_client:
        yield async_client


TokenFactory = Callable[..., str]


@pytest.fixture
def make_token() -> TokenFactory:
    """Mint Supabase-shaped access tokens, valid or deliberately not."""

    def factory(
        user_id: UUID = OWNER_ID,
        *,
        secret: str = JWT_SECRET,
        audience: str | None = JWT_AUDIENCE,
        algorithm: str = "HS256",
        expires_in: dt.timedelta = dt.timedelta(hours=1),
        email: str | None = "ada@example.com",
        role: str | None = "authenticated",
        subject: str | None = None,
        omit_exp: bool = False,
    ) -> str:
        now = dt.datetime.now(tz=dt.UTC)
        claims: dict[str, Any] = {
            "sub": subject if subject is not None else str(user_id),
            "iat": now,
        }
        if not omit_exp:
            claims["exp"] = now + expires_in
        if audience is not None:
            claims["aud"] = audience
        if email is not None:
            claims["email"] = email
        if role is not None:
            claims["role"] = role
        return jwt.encode(claims, secret, algorithm=algorithm)

    return factory


@pytest.fixture
def auth_headers(make_token: TokenFactory) -> Callable[..., dict[str, str]]:
    """Authorization header for a given user."""

    def factory(user_id: UUID = OWNER_ID, **kwargs: Any) -> dict[str, str]:
        return {"Authorization": f"Bearer {make_token(user_id, **kwargs)}"}

    return factory


# Structural conformance: if a fake stops satisfying the protocol the real code
# depends on, this fails at type-check time rather than at runtime in a test.
_: WorkspaceRepository = FakeWorkspaceRepository([])
__: ProfileRepository = FakeProfileRepository({})
