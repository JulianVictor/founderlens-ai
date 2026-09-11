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
    DocumentNotFoundError,
    DocumentStorageError,
    DocumentTooLargeError,
    DuplicateDocumentError,
    EmptyDocumentError,
    InsufficientWorkspaceRoleError,
    ProfileNotFoundError,
    UnsupportedDocumentTypeError,
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


async def _handle_document_not_found(request: Request, exc: DocumentNotFoundError) -> JSONResponse:
    # Same body whether the document does not exist or belongs to a workspace
    # the caller cannot see -- the lookup is workspace-scoped, so the two are
    # not distinguishable here even in principle.
    return _problem(status.HTTP_404_NOT_FOUND, "Document not found")


async def _handle_unsupported_type(
    request: Request, exc: UnsupportedDocumentTypeError
) -> JSONResponse:
    # 415, not 400: the request was well-formed, its payload is a type this
    # endpoint does not accept.
    return _problem(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        f"Unsupported document type. Accepted: {', '.join(exc.accepted)}.",
    )


async def _handle_document_too_large(request: Request, exc: DocumentTooLargeError) -> JSONResponse:
    return _problem(
        status.HTTP_413_CONTENT_TOO_LARGE,
        f"Document exceeds the {exc.limit_bytes // (1024 * 1024)} MB limit",
    )


async def _handle_empty_document(request: Request, exc: EmptyDocumentError) -> JSONResponse:
    return _problem(status.HTTP_400_BAD_REQUEST, "Document is empty")


async def _handle_duplicate_document(request: Request, exc: DuplicateDocumentError) -> JSONResponse:
    # 409 with the id of what is already there, so a client can navigate to the
    # existing document instead of treating this as a dead end.
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": "This document is already in the workspace",
            "existing_document_id": str(exc.existing_document_id),
        },
    )


async def _handle_storage_error(request: Request, exc: DocumentStorageError) -> JSONResponse:
    # A dependency failed, not the caller. 502 rather than 500 says the fault is
    # downstream of this service, and rather than 4xx says retrying is sensible.
    logger.error("Document storage %s failed for %s", exc.operation, request.url.path)
    return _problem(
        status.HTTP_502_BAD_GATEWAY,
        "Document storage is unavailable. Please try again.",
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Wire domain exceptions to their HTTP representations."""
    app.add_exception_handler(AuthenticationError, _handle_authentication_error)  # type: ignore[arg-type]
    app.add_exception_handler(WorkspaceNotFoundError, _handle_workspace_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(InsufficientWorkspaceRoleError, _handle_insufficient_role)  # type: ignore[arg-type]
    app.add_exception_handler(ProfileNotFoundError, _handle_profile_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(DocumentNotFoundError, _handle_document_not_found)  # type: ignore[arg-type]
    app.add_exception_handler(UnsupportedDocumentTypeError, _handle_unsupported_type)  # type: ignore[arg-type]
    app.add_exception_handler(DocumentTooLargeError, _handle_document_too_large)  # type: ignore[arg-type]
    app.add_exception_handler(EmptyDocumentError, _handle_empty_document)  # type: ignore[arg-type]
    app.add_exception_handler(DuplicateDocumentError, _handle_duplicate_document)  # type: ignore[arg-type]
    app.add_exception_handler(DocumentStorageError, _handle_storage_error)  # type: ignore[arg-type]
