"""Translation of domain exceptions into HTTP responses.

Registered once on the application so that route handlers and services stay
free of HTTP status codes. A service says "not a member"; this module decides
that means 404.
"""

from __future__ import annotations

import logging
from typing import Final

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AuthenticationError,
    InsufficientWorkspaceRoleError,
    ProfileNotFoundError,
    WorkspaceNotFoundError,
)

logger: Final = logging.getLogger(__name__)


def _problem(status_code: int, detail: str, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail}, headers=headers)


async def _handle_authentication_error(request: Request, exc: AuthenticationError) -> JSONResponse:
    # Logged at info: a rejected token is routine (an expired session), not a
    # fault. The reason stays here; the caller is told only that it failed.
    logger.info("Rejected unauthenticated request to %s", request.url.path)
    return _problem(
        status.HTTP_401_UNAUTHORIZED,
        exc.message,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _handle_workspace_not_found(
    request: Request, exc: WorkspaceNotFoundError
) -> JSONResponse:
    return _problem(status.HTTP_404_NOT_FOUND, "Workspace not found")


async def _handle_insufficient_role(
    request: Request, exc: InsufficientWorkspaceRoleError
) -> JSONResponse:
    return _problem(
        status.HTTP_403_FORBIDDEN,
        f"This action requires the {exc.required!r} role in this workspace",
    )


async def _handle_profile_not_found(request: Request, exc: ProfileNotFoundError) -> JSONResponse:
    # An authenticated user without a profile means the signup trigger did not
    # run. That is a broken deployment, so it is logged as an error and
    # reported as 500 rather than quietly returning a partial identity.
    logger.error("Authenticated user %s has no profile row", exc.user_id)
    return _problem(
        status.HTTP_500_INTERNAL_SERVER_ERROR,
        "Account is not fully provisioned",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire domain exceptions to their HTTP representations."""
    app.add_exception_handler(AuthenticationError, _handle_authentication_error)  # type: ignore[arg-type]
    app.add_exception_handler(WorkspaceNotFoundError, _handle_workspace_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(InsufficientWorkspaceRoleError, _handle_insufficient_role)  # type: ignore[arg-type]
    app.add_exception_handler(ProfileNotFoundError, _handle_profile_not_found)  # type: ignore[arg-type]
