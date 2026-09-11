"""API contracts for documents."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.document import Document, DocumentStatus


class DocumentResponse(BaseModel):
    """One document, as seen by a member of its workspace.

    ``storage_path`` and ``checksum_sha256`` are deliberately not exposed. The
    object key is an internal detail of where bytes happen to live, and
    publishing it invites clients to build URLs against the bucket instead of
    going through the API -- which is where the membership check lives.
    """

    id: UUID
    workspace_id: UUID
    uploaded_by: UUID
    filename: str
    mime_type: str
    size_bytes: int
    status: DocumentStatus
    error_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_document(cls, document: Document) -> DocumentResponse:
        return cls(
            id=document.id,
            workspace_id=document.workspace_id,
            uploaded_by=document.uploaded_by,
            filename=document.filename,
            mime_type=document.mime_type,
            size_bytes=document.size_bytes,
            status=document.status,
            error_message=document.error_message,
            created_at=document.created_at,
            updated_at=document.updated_at,
        )


class DocumentListResponse(BaseModel):
    """Envelope for the document listing.

    An object rather than a bare array so that pagination can be added without
    breaking clients.
    """

    documents: list[DocumentResponse]
