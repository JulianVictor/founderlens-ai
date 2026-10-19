"""Persistence shapes for ingestion jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class IngestionStage(StrEnum):
    """A stage of the deterministic pipeline.

    Mirrors ``public.ingestion_stage``. Declaration order is execution order,
    and :attr:`order` depends on it, so stages must be added in the position
    they actually run rather than appended.

    ``EMBED`` and ``GRAPH`` are deliberately absent: a stage the pipeline
    cannot reach would make ``current_stage`` a claim rather than a fact.
    """

    UPLOAD = "upload"
    EXTRACT = "extract"
    CLEAN = "clean"
    CHUNK = "chunk"
    PERSIST = "persist"

    @property
    def order(self) -> int:
        """Position in the pipeline, 0-based."""
        return list(IngestionStage).index(self)


class IngestionJobStatus(StrEnum):
    """Lifecycle of one run. Mirrors ``public.ingestion_job_status``."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_active(self) -> bool:
        """Whether the job still holds the document.

        The same set the database's one-active-job-per-document index uses, so
        the two cannot disagree about what "active" means.
        """
        return self in {IngestionJobStatus.QUEUED, IngestionJobStatus.RUNNING}

    @property
    def is_terminal(self) -> bool:
        return not self.is_active


@dataclass(frozen=True, slots=True)
class IngestionJob:
    """A row of ``public.ingestion_jobs``."""

    id: UUID
    workspace_id: UUID
    document_id: UUID
    status: IngestionJobStatus
    current_stage: IngestionStage
    attempt: int
    error_message: str | None
    chunk_count: int | None
    pipeline_version: str
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
