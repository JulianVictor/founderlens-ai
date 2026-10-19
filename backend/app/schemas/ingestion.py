"""API contracts for ingestion."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.document import Document, DocumentStatus
from app.models.ingestion import IngestionJob, IngestionJobStatus, IngestionStage


class IngestionJobResponse(BaseModel):
    """One run of the pipeline, as reported to a client."""

    id: UUID
    document_id: UUID
    status: IngestionJobStatus
    current_stage: IngestionStage
    attempt: int
    error_message: str | None
    chunk_count: int | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    @classmethod
    def from_job(cls, job: IngestionJob) -> IngestionJobResponse:
        return cls(
            id=job.id,
            document_id=job.document_id,
            status=job.status,
            current_stage=job.current_stage,
            attempt=job.attempt,
            error_message=job.error_message,
            chunk_count=job.chunk_count,
            started_at=job.started_at,
            completed_at=job.completed_at,
            created_at=job.created_at,
        )


class ProcessingStatusResponse(BaseModel):
    """Everything a client needs to render a document's progress.

    The document's status and the job are both included because they answer
    different questions: the document says whether it is usable, and the job
    says what happened on the most recent attempt. A document can be ``ready``
    while its latest job failed -- an earlier run succeeded and a later retry
    did not -- and a client that saw only one of the two would mislead.
    """

    document_id: UUID
    status: DocumentStatus
    error_message: str | None
    #: Chunks currently stored. Non-zero only once a run has reached PERSIST.
    chunk_count: int
    #: Most recent run, or null if the document has never been processed.
    job: IngestionJobResponse | None

    @classmethod
    def build(
        cls, document: Document, job: IngestionJob | None, chunk_count: int
    ) -> ProcessingStatusResponse:
        return cls(
            document_id=document.id,
            status=document.status,
            error_message=document.error_message,
            chunk_count=chunk_count,
            job=None if job is None else IngestionJobResponse.from_job(job),
        )
