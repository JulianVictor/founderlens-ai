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


class DocumentNotFoundError(FounderLensError):
    """No such document in this workspace.

    As with workspaces, "does not exist" and "exists in a workspace you are not
    a member of" are the same error. The repository is scoped by workspace, so
    the two cases are not even distinguishable by the time this is raised.
    """

    def __init__(self, document_id: UUID) -> None:
        super().__init__(f"Document {document_id} not found")
        self.document_id = document_id


class UnsupportedDocumentTypeError(FounderLensError):
    """The uploaded bytes are not a type FounderLens accepts.

    Raised on what the file *is*, never on what it was called.
    """

    def __init__(self, filename: str, accepted: tuple[str, ...]) -> None:
        super().__init__(f"{filename!r} is not an accepted document type")
        self.filename = filename
        self.accepted = accepted


class DocumentTooLargeError(FounderLensError):
    """The upload exceeds the configured size limit."""

    def __init__(self, limit_bytes: int) -> None:
        super().__init__(f"Document exceeds the {limit_bytes} byte limit")
        self.limit_bytes = limit_bytes


class EmptyDocumentError(FounderLensError):
    """The upload contained no bytes.

    Distinguished from an unsupported type because the fix is different: an
    empty file is usually a failed read on the client, not a wrong format.
    """


class DuplicateDocumentError(FounderLensError):
    """These exact bytes are already in this workspace.

    Not an error in the sense of something going wrong -- it is how re-uploading
    resolves to the document that already exists, rather than counting one
    source twice and skewing retrieval.
    """

    def __init__(self, existing_document_id: UUID) -> None:
        super().__init__(f"Document already exists as {existing_document_id}")
        self.existing_document_id = existing_document_id


class DocumentStorageError(FounderLensError):
    """Supabase Storage could not be reached, or refused the operation.

    A dependency failure rather than a caller mistake, so it must not be
    reported as a 4xx: the client did nothing wrong and retrying may work.
    """

    def __init__(self, operation: str) -> None:
        super().__init__(f"Document storage {operation} failed")
        self.operation = operation


class DocumentParseError(FounderLensError):
    """A document could not be turned into text.

    Distinct from :class:`UnsupportedDocumentTypeError`: the type was accepted,
    and the file still could not be read -- a corrupt PDF, an encrypted one, or
    bytes that are not valid UTF-8 past the prefix the upload check sampled.
    Raised during ingestion rather than upload, so it becomes a failed document
    with an explanation rather than a rejected request.
    """

    def __init__(self, document_id: UUID, reason: str) -> None:
        super().__init__(f"Could not parse document {document_id}: {reason}")
        self.document_id = document_id
        self.reason = reason
