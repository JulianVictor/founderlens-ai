"""Domain exceptions.

Services raise these; the API layer maps them onto HTTP status codes in
:mod:`app.api.errors`. Keeping them free of HTTP concepts is what lets a service
be called from somewhere that is not a request handler -- a worker, a CLI, a
test -- without dragging FastAPI along.
"""

from __future__ import annotations

from uuid import UUID


class FounderLensError(Exception):
    """Base class for every domain error raised by this application."""


class AuthenticationError(FounderLensError):
    """The caller could not be identified.

    Raised when a token is absent, malformed, expired, or fails verification.
    Deliberately carries no detail about *which*: telling an unauthenticated
    caller why their token was rejected helps an attacker more than a user.
    """

    def __init__(self, message: str = "Could not validate credentials") -> None:
        super().__init__(message)
        self.message = message


class WorkspaceNotFoundError(FounderLensError):
    """The workspace does not exist, or the caller is not a member of it.

    These two cases are deliberately indistinguishable. Returning "forbidden"
    for a workspace the caller is not a member of would confirm that the
    workspace exists, which leaks the existence of other users' data to anyone
    willing to guess UUIDs.
    """

    def __init__(self, workspace_id: UUID) -> None:
        super().__init__(f"Workspace {workspace_id} not found")
        self.workspace_id = workspace_id


class InsufficientWorkspaceRoleError(FounderLensError):
    """The caller is a member of the workspace, but not at a high enough role.

    Distinct from :class:`WorkspaceNotFoundError` on purpose: the caller already
    knows this workspace exists, so a 403 discloses nothing new and is far more
    useful than a 404.
    """

    def __init__(self, workspace_id: UUID, required: str, actual: str) -> None:
        super().__init__(
            f"Workspace {workspace_id} requires role {required!r}, caller has {actual!r}"
        )
        self.workspace_id = workspace_id
        self.required = required
        self.actual = actual


class ProfileNotFoundError(FounderLensError):
    """An authenticated user has no profile row.

    Should be unreachable: the signup trigger creates a profile for every
    auth.users row in the same transaction. If it happens, the trigger was not
    installed or was bypassed, and that is worth surfacing as a server error
    rather than papering over.
    """

    def __init__(self, user_id: UUID) -> None:
        super().__init__(f"No profile for user {user_id}")
        self.user_id = user_id
