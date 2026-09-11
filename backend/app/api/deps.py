"""Reusable FastAPI dependencies.

Two of these matter beyond this phase:

* :func:`get_current_user` -- put it on a route and the route is authenticated.
* :func:`WorkspaceMember` -- annotate a ``workspace_id`` path parameter with it
  and the route is authenticated *and* scoped to a workspace the caller belongs
  to, with their role already resolved.

The intent is that a future route cannot accidentally skip the workspace check,
because obtaining the workspace is the same act as being authorised for it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import lru_cache
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Path, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.db import Database
from app.core.exceptions import AuthenticationError
from app.core.security import AuthenticatedUser, SupabaseJWTVerifier
from app.models.workspace import WorkspaceMembership, WorkspaceRole
from app.repositories.profile_repository import (
    PostgresProfileRepository,
    ProfileRepository,
)
from app.repositories.workspace_repository import (
    PostgresWorkspaceRepository,
    WorkspaceRepository,
)
from app.services.profile_service import ProfileService
from app.services.workspace_service import WorkspaceService

SettingsDep = Annotated[Settings, Depends(get_settings)]

# auto_error=False so that a missing Authorization header reaches our own code.
# With auto_error=True, FastAPI answers 403 for an absent header, which is the
# wrong status: the caller has not failed an authorization check, they have not
# authenticated at all.
_bearer_scheme = HTTPBearer(auto_error=False, description="Supabase access token")

CredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


@lru_cache(maxsize=1)
def _cached_verifier(secret: str, audience: str, leeway_seconds: int) -> SupabaseJWTVerifier:
    return SupabaseJWTVerifier(secret=secret, audience=audience, leeway_seconds=leeway_seconds)


def get_verifier(settings: SettingsDep) -> SupabaseJWTVerifier:
    """The process-wide token verifier.

    Cached on its inputs rather than rebuilt per request: constructing it
    validates configuration, and doing that on every request would be waste.
    """
    return _cached_verifier(
        settings.supabase_jwt_secret.get_secret_value(),
        settings.supabase_jwt_audience,
        settings.supabase_jwt_leeway_seconds,
    )


VerifierDep = Annotated[SupabaseJWTVerifier, Depends(get_verifier)]


def get_current_user(
    credentials: CredentialsDep,
    verifier: VerifierDep,
) -> AuthenticatedUser:
    """Identify the caller from their bearer token, or reject the request.

    Raises:
        AuthenticationError: no credentials, or credentials that do not verify.
            Mapped to 401 in :mod:`app.api.errors`.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("Not authenticated")
    return verifier.verify(credentials.credentials)


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]


def get_database(request: Request) -> Database:
    """The pool owned by the running application."""
    database: Database = request.app.state.database
    return database


DatabaseDep = Annotated[Database, Depends(get_database)]


def get_workspace_repository(database: DatabaseDep) -> WorkspaceRepository:
    return PostgresWorkspaceRepository(database.pool)


def get_profile_repository(database: DatabaseDep) -> ProfileRepository:
    return PostgresProfileRepository(database.pool)


def get_workspace_service(
    repository: Annotated[WorkspaceRepository, Depends(get_workspace_repository)],
) -> WorkspaceService:
    return WorkspaceService(repository)


def get_profile_service(
    repository: Annotated[ProfileRepository, Depends(get_profile_repository)],
) -> ProfileService:
    return ProfileService(repository)


WorkspaceServiceDep = Annotated[WorkspaceService, Depends(get_workspace_service)]
ProfileServiceDep = Annotated[ProfileService, Depends(get_profile_service)]


async def get_workspace_membership(
    workspace_id: Annotated[UUID, Path(description="Workspace being accessed")],
    user: CurrentUser,
    service: WorkspaceServiceDep,
) -> WorkspaceMembership:
    """Resolve the caller's membership of the ``workspace_id`` in the path.

    Any route that declares this dependency is workspace-scoped: it cannot run
    unless the caller is a member, and it receives the workspace and the
    caller's role without a second query.

    Raises:
        WorkspaceNotFoundError: not a member, or no such workspace -- 404.
    """
    return await service.require_membership(workspace_id, user.id)


#: Annotate a route parameter with this to require workspace membership.
#: ``async def route(membership: WorkspaceMember) -> ...``
WorkspaceMember = Annotated[WorkspaceMembership, Depends(get_workspace_membership)]


def require_workspace_role(
    minimum_role: WorkspaceRole,
) -> Callable[..., Awaitable[WorkspaceMembership]]:
    """Build a dependency requiring at least ``minimum_role`` in the workspace.

    For routes where membership alone is not enough::

        OwnerOnly = Annotated[
            WorkspaceMembership, Depends(require_workspace_role(WorkspaceRole.OWNER))
        ]

    Raises (from the returned dependency):
        WorkspaceNotFoundError: not a member -- 404.
        InsufficientWorkspaceRoleError: a member, but too junior -- 403.
    """

    async def dependency(
        workspace_id: Annotated[UUID, Path(description="Workspace being accessed")],
        user: CurrentUser,
        service: WorkspaceServiceDep,
    ) -> WorkspaceMembership:
        return await service.require_membership(workspace_id, user.id, minimum_role=minimum_role)

    return dependency
