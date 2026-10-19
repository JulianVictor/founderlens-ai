"""Ingestion job persistence.

Scoped by workspace like every other repository. The one unusual method is
:meth:`IngestionJobRepository.claim`, which exists because "is there already a
job running for this document" cannot be answered correctly by reading and then
writing -- two callers can both read "no" before either writes.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg

from app.models.ingestion import IngestionJob, IngestionJobStatus, IngestionStage

_COLUMNS = """
    id, workspace_id, document_id, status, current_stage, attempt,
    error_message, chunk_count, pipeline_version, started_at, completed_at,
    created_at, updated_at
"""

# The insert relies on the partial unique index
# `ingestion_jobs_one_active_per_document`: if another job for this document is
# already queued or running, the insert violates it and the caller learns that
# it lost the race. ON CONFLICT DO NOTHING turns that into an empty result
# rather than an exception, because losing the race is an expected outcome of a
# double-click, not an error.
_CLAIM = """
    insert into public.ingestion_jobs (
        workspace_id, document_id, attempt, pipeline_version
    )
    select $1, $2,
           (select count(*) + 1 from public.ingestion_jobs where document_id = $2),
           $3
    on conflict do nothing
    returning id, workspace_id, document_id, status, current_stage, attempt,
              error_message, chunk_count, pipeline_version, started_at,
              completed_at, created_at, updated_at
"""

_GET = """
    select id, workspace_id, document_id, status, current_stage, attempt,
           error_message, chunk_count, pipeline_version, started_at,
           completed_at, created_at, updated_at
      from public.ingestion_jobs
     where workspace_id = $1
       and id = $2
"""

_LATEST_FOR_DOCUMENT = """
    select id, workspace_id, document_id, status, current_stage, attempt,
           error_message, chunk_count, pipeline_version, started_at,
           completed_at, created_at, updated_at
      from public.ingestion_jobs
     where workspace_id = $1
       and document_id = $2
     order by created_at desc, id desc
     limit 1
"""

_ACTIVE_FOR_DOCUMENT = """
    select id, workspace_id, document_id, status, current_stage, attempt,
           error_message, chunk_count, pipeline_version, started_at,
           completed_at, created_at, updated_at
      from public.ingestion_jobs
     where workspace_id = $1
       and document_id = $2
       and status in ('queued', 'running')
     limit 1
"""

# Only a queued job may start, so a second runner picking up the same id finds
# nothing to update and stops rather than running the pipeline twice.
_START = """
    update public.ingestion_jobs
       set status = 'running',
           current_stage = $2,
           started_at = now()
     where id = $1
       and status = 'queued'
    returning id, workspace_id, document_id, status, current_stage, attempt,
              error_message, chunk_count, pipeline_version, started_at,
              completed_at, created_at, updated_at
"""

_ADVANCE = """
    update public.ingestion_jobs
       set current_stage = $2
     where id = $1
       and status = 'running'
    returning id, workspace_id, document_id, status, current_stage, attempt,
              error_message, chunk_count, pipeline_version, started_at,
              completed_at, created_at, updated_at
"""

_FINISH = """
    update public.ingestion_jobs
       set status = $2,
           error_message = $3,
           chunk_count = $4,
           completed_at = now()
     where id = $1
       and status in ('queued', 'running')
    returning id, workspace_id, document_id, status, current_stage, attempt,
              error_message, chunk_count, pipeline_version, started_at,
              completed_at, created_at, updated_at
"""


class IngestionJobRepository(Protocol):
    """Read and write access to ingestion jobs."""

    async def claim(
        self, workspace_id: UUID, document_id: UUID, pipeline_version: str
    ) -> IngestionJob | None:
        """Create a job for the document, if none is already active.

        Returns ``None`` when another job is already queued or running. The
        check and the write are one statement, so two concurrent callers
        cannot both succeed.
        """
        ...

    async def get(self, workspace_id: UUID, job_id: UUID) -> IngestionJob | None: ...

    async def latest_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> IngestionJob | None:
        """The most recent job, whatever its state."""
        ...

    async def active_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> IngestionJob | None:
        """The queued or running job, if there is one."""
        ...

    async def start(self, job_id: UUID, stage: IngestionStage) -> IngestionJob | None:
        """Move a queued job to running. ``None`` if it was not queued."""
        ...

    async def advance(self, job_id: UUID, stage: IngestionStage) -> IngestionJob | None:
        """Record that a running job reached ``stage``."""
        ...

    async def finish(
        self,
        job_id: UUID,
        status: IngestionJobStatus,
        *,
        error_message: str | None = None,
        chunk_count: int | None = None,
    ) -> IngestionJob | None:
        """Move an active job to a terminal state."""
        ...


def _to_job(record: asyncpg.Record) -> IngestionJob:
    return IngestionJob(
        id=record["id"],
        workspace_id=record["workspace_id"],
        document_id=record["document_id"],
        status=IngestionJobStatus(record["status"]),
        current_stage=IngestionStage(record["current_stage"]),
        attempt=record["attempt"],
        error_message=record["error_message"],
        chunk_count=record["chunk_count"],
        pipeline_version=record["pipeline_version"],
        started_at=record["started_at"],
        completed_at=record["completed_at"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


class PostgresIngestionJobRepository:
    """:class:`IngestionJobRepository` backed by Supabase Postgres."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def claim(
        self, workspace_id: UUID, document_id: UUID, pipeline_version: str
    ) -> IngestionJob | None:
        record = await self._pool.fetchrow(_CLAIM, workspace_id, document_id, pipeline_version)
        return None if record is None else _to_job(record)

    async def get(self, workspace_id: UUID, job_id: UUID) -> IngestionJob | None:
        record = await self._pool.fetchrow(_GET, workspace_id, job_id)
        return None if record is None else _to_job(record)

    async def latest_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> IngestionJob | None:
        record = await self._pool.fetchrow(_LATEST_FOR_DOCUMENT, workspace_id, document_id)
        return None if record is None else _to_job(record)

    async def active_for_document(
        self, workspace_id: UUID, document_id: UUID
    ) -> IngestionJob | None:
        record = await self._pool.fetchrow(_ACTIVE_FOR_DOCUMENT, workspace_id, document_id)
        return None if record is None else _to_job(record)

    async def start(self, job_id: UUID, stage: IngestionStage) -> IngestionJob | None:
        record = await self._pool.fetchrow(_START, job_id, stage.value)
        return None if record is None else _to_job(record)

    async def advance(self, job_id: UUID, stage: IngestionStage) -> IngestionJob | None:
        record = await self._pool.fetchrow(_ADVANCE, job_id, stage.value)
        return None if record is None else _to_job(record)

    async def finish(
        self,
        job_id: UUID,
        status: IngestionJobStatus,
        *,
        error_message: str | None = None,
        chunk_count: int | None = None,
    ) -> IngestionJob | None:
        record = await self._pool.fetchrow(
            _FINISH, job_id, status.value, error_message, chunk_count
        )
        return None if record is None else _to_job(record)
