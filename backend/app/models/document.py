"""Persistence shapes for uploaded documents."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class DocumentStatus(StrEnum):
    """Where a document has got to in the ingestion pipeline.

    Mirrors the ``public.document_status`` enum in Postgres.
    """

    #: Accepted and stored. Nothing has been derived from it yet.
    UPLOADED = "uploaded"
    #: An ingestion job is running over it.
    PROCESSING = "processing"
    #: Ingested: chunks, embeddings and graph data exist.
    READY = "ready"
    #: Ingestion failed. ``error_message`` says why.
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        """Whether no further transition happens without a new job."""
        return self in {DocumentStatus.READY, DocumentStatus.FAILED}


@dataclass(frozen=True, slots=True)
class Document:
    """A row of ``public.documents``."""

    id: UUID
    workspace_id: UUID
    uploaded_by: UUID
    filename: str
    storage_path: str
    mime_type: str
    size_bytes: int
    checksum_sha256: str
    status: DocumentStatus
    error_message: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class NewDocument:
    """A document about to be recorded.

    Separate from :class:`Document` because the database supplies the timestamps
    and the status; modelling those as optional on one class would mean every
    reader had to handle a state that never reaches them.

    The id is supplied by the application rather than defaulted by Postgres.
    It has to be: the storage key is built from it, and the bytes are written
    before the row exists, so the id must be known before the insert. A
    server-generated UUIDv4 is the same value the column default would have
    produced -- the point is only *when* it is chosen.
    """

    id: UUID
    workspace_id: UUID
    uploaded_by: UUID
    filename: str
    storage_path: str
    mime_type: str
    size_bytes: int
    checksum_sha256: str
