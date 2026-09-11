"""Documents within a workspace.

Every route here declares ``WorkspaceMember``, so none of them can run unless
the caller is a member of the workspace named in the path. The membership check
is a consequence of the route's signature rather than a line of code inside it,
which is what makes it impossible to forget.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, File, Path, UploadFile, status

from app.api.deps import DocumentServiceDep, WorkspaceMember
from app.core.exceptions import DocumentTooLargeError
from app.schemas.document import DocumentListResponse, DocumentResponse

router = APIRouter(
    prefix="/workspaces/{workspace_id}/documents",
    tags=["documents"],
)

DocumentId = Annotated[UUID, Path(description="Document being addressed")]

#: Read the upload in chunks so that a large body is rejected partway through
#: rather than after it has all been buffered. Without this, the size limit is
#: enforced only once the server has already paid the memory cost of the file.
_CHUNK_BYTES = 64 * 1024


async def _read_capped(upload: UploadFile, limit: int) -> bytes:
    """Read at most ``limit`` bytes, refusing anything larger.

    Reads one chunk beyond the limit so that a file of exactly ``limit`` bytes
    is accepted and one byte more is not.
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise DocumentTooLargeError(limit)
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document to a workspace",
)
async def upload_document(
    membership: WorkspaceMember,
    documents: DocumentServiceDep,
    file: Annotated[UploadFile, File(description="PDF, plain text or Markdown")],
) -> DocumentResponse:
    """Accept a document into the workspace.

    The uploaded content type and filename extension are both recorded as claims
    and neither is believed: the service determines the type from the bytes.
    """
    data = await _read_capped(file, documents.max_document_bytes)
    document = await documents.upload(
        workspace_id=membership.workspace.id,
        uploaded_by=membership.user_id,
        filename=file.filename or "untitled",
        data=data,
    )
    return DocumentResponse.from_document(document)


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="List a workspace's documents",
)
async def list_documents(
    membership: WorkspaceMember, documents: DocumentServiceDep
) -> DocumentListResponse:
    """List documents, newest first.

    Scoped to the workspace resolved from the path and verified for the caller.
    """
    found = await documents.list_for_workspace(membership.workspace.id)
    return DocumentListResponse(documents=[DocumentResponse.from_document(d) for d in found])


@router.get(
    "/{document_id}",
    response_model=DocumentResponse,
    summary="Fetch one document's metadata",
)
async def get_document(
    membership: WorkspaceMember,
    documents: DocumentServiceDep,
    document_id: DocumentId,
) -> DocumentResponse:
    """Fetch a single document.

    A document id belonging to another workspace is a 404 here, because the
    lookup is scoped by workspace and never finds it in the first place.
    """
    document = await documents.get(membership.workspace.id, document_id)
    return DocumentResponse.from_document(document)


@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document and its stored bytes",
)
async def delete_document(
    membership: WorkspaceMember,
    documents: DocumentServiceDep,
    document_id: DocumentId,
) -> None:
    """Delete a document, its stored object and its derived data."""
    await documents.delete(membership.workspace.id, document_id)
