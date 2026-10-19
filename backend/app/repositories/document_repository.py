"""Document metadata persistence.

Every method takes ``workspace_id``. There is no "get document by id" without
one, and that is the point: an unscoped lookup is the exact shape of query that
lets a guessed UUID return another workspace's row. Because the scope is part of
the query rather than a check applied to its result, a caller cannot forget it
-- there is no method to call that would omit it.

As with workspaces, the SQL is written out rather than assembled, so that it is
visible at a glance that nothing but bound parameters reaches the statement.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg

from app.models.document import Document, DocumentStatus, NewDocument

_INSERT = """
    insert into public.documents (
        id, workspace_id, uploaded_by, filename, storage_path,
        mime_type, size_bytes, checksum_sha256
    )
    values ($1, $2, $3, $4, $5, $6, $7, $8)
    returning id, workspace_id, uploaded_by, filename, storage_path, mime_type,
              size_bytes, checksum_sha256, status, error_message,
              created_at, updated_at
"""

_LIST = """
    select id, workspace_id, uploaded_by, filename, storage_path, mime_type,
           size_bytes, checksum_sha256, status, error_message,
           created_at, updated_at
      from public.documents
     where workspace_id = $1
     order by created_at desc, id desc
"""

_GET = """
    select id, workspace_id, uploaded_by, filename, storage_path, mime_type,
           size_bytes, checksum_sha256, status, error_message,
           created_at, updated_at
      from public.documents
     where workspace_id = $1
       and id = $2
"""

_FIND_BY_CHECKSUM = """
    select id, workspace_id, uploaded_by, filename, storage_path, mime_type,
           size_bytes, checksum_sha256, status, error_message,
           created_at, updated_at
      from public.documents
     where workspace_id = $1
       and checksum_sha256 = $2
"""

# error_message is cleared unless the new status is 'failed', because the
# documents_error_only_when_failed constraint rejects a stale explanation and,
# more importantly, because showing one on a ready document would be a lie.
_UPDATE_STATUS = """
    update public.documents
       set status = $3,
           error_message = case when $3 = 'failed' then $4::text else null end
     where workspace_id = $1
       and id = $2
    returning id, workspace_id, uploaded_by, filename, storage_path, mime_type,
              size_bytes, checksum_sha256, status, error_message,
              created_at, updated_at
"""

_DELETE = """
    delete from public.documents
     where workspace_id = $1
       and id = $2
    returning id
"""


class DocumentRepository(Protocol):
    """Read and write access to document metadata, always workspace-scoped."""

    async def create(self, document: NewDocument) -> Document:
        """Record a stored document and return the persisted row."""
        ...

    async def list_for_workspace(self, workspace_id: UUID) -> list[Document]:
        """Every document in the workspace, newest first."""
        ...

    async def get(self, workspace_id: UUID, document_id: UUID) -> Document | None:
        """One document, or ``None`` if it is not in this workspace."""
        ...

    async def find_by_checksum(self, workspace_id: UUID, checksum_sha256: str) -> Document | None:
        """An existing document with the same content, if there is one."""
        ...

    async def update_status(
        self,
        workspace_id: UUID,
        document_id: UUID,
        status: DocumentStatus,
        *,
        error_message: str | None = None,
    ) -> Document | None:
        """Set the document's processing status.

        ``error_message`` is kept only for the failed status; any other status
        clears it, so a document that recovers does not keep explaining a
        failure that no longer applies.
        """
        ...

    async def delete(self, workspace_id: UUID, document_id: UUID) -> bool:
        """Remove the row. ``False`` if it was not there to remove."""
        ...


def _to_document(record: asyncpg.Record) -> Document:
    return Document(
        id=record["id"],
        workspace_id=record["workspace_id"],
        uploaded_by=record["uploaded_by"],
        filename=record["filename"],
        storage_path=record["storage_path"],
        mime_type=record["mime_type"],
        size_bytes=record["size_bytes"],
        checksum_sha256=record["checksum_sha256"],
        status=DocumentStatus(record["status"]),
        error_message=record["error_message"],
        created_at=record["created_at"],
        updated_at=record["updated_at"],
    )


class PostgresDocumentRepository:
    """:class:`DocumentRepository` backed by Supabase Postgres."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, document: NewDocument) -> Document:
        record = await self._pool.fetchrow(
            _INSERT,
            document.id,
            document.workspace_id,
            document.uploaded_by,
            document.filename,
            document.storage_path,
            document.mime_type,
            document.size_bytes,
            document.checksum_sha256,
        )
        if record is None:  # pragma: no cover - INSERT ... RETURNING always returns
            raise RuntimeError("Insert returned no row")
        return _to_document(record)

    async def list_for_workspace(self, workspace_id: UUID) -> list[Document]:
        records = await self._pool.fetch(_LIST, workspace_id)
        return [_to_document(record) for record in records]

    async def get(self, workspace_id: UUID, document_id: UUID) -> Document | None:
        record = await self._pool.fetchrow(_GET, workspace_id, document_id)
        return None if record is None else _to_document(record)

    async def find_by_checksum(self, workspace_id: UUID, checksum_sha256: str) -> Document | None:
        record = await self._pool.fetchrow(_FIND_BY_CHECKSUM, workspace_id, checksum_sha256)
        return None if record is None else _to_document(record)

    async def update_status(
        self,
        workspace_id: UUID,
        document_id: UUID,
        status: DocumentStatus,
        *,
        error_message: str | None = None,
    ) -> Document | None:
        record = await self._pool.fetchrow(
            _UPDATE_STATUS, workspace_id, document_id, status.value, error_message
        )
        return None if record is None else _to_document(record)

    async def delete(self, workspace_id: UUID, document_id: UUID) -> bool:
        record = await self._pool.fetchrow(_DELETE, workspace_id, document_id)
        return record is not None
