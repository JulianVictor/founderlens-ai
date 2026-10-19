"""Chunk persistence.

The interesting operation is :meth:`ChunkRepository.replace_for_document`,
which is what makes reprocessing idempotent. It is a *replace*, not an append:
after it runs, the document's chunks are exactly the set it was given.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg

from app.ai.ingestion.models import DocumentChunk

# Deleting first, and only what the new set does not contain, is what makes the
# insert below safe: an old chunk occupying index 3 under a different id would
# otherwise collide with the new one on (document_id, chunk_index).
_DELETE_ABSENT = """
    delete from public.document_chunks
     where document_id = $1
       and not (id = any($2::uuid[]))
"""

_DELETE_ALL = """
    delete from public.document_chunks
     where document_id = $1
"""

# ON CONFLICT on the deterministic id: a chunk whose content and position are
# unchanged is updated in place rather than recreated, so created_at still says
# when it first appeared. That is the visible evidence that reprocessing an
# unchanged document did not churn the corpus.
_UPSERT = """
    insert into public.document_chunks (
        id, workspace_id, document_id, chunk_index, text, token_count,
        page_number, page_numbers, start_offset, end_offset,
        source_start_offset, source_end_offset, content_hash, tokenizer,
        ingestion_job_id
    )
    values ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
    on conflict (id) do update
       set chunk_index = excluded.chunk_index,
           text = excluded.text,
           token_count = excluded.token_count,
           page_number = excluded.page_number,
           page_numbers = excluded.page_numbers,
           start_offset = excluded.start_offset,
           end_offset = excluded.end_offset,
           source_start_offset = excluded.source_start_offset,
           source_end_offset = excluded.source_end_offset,
           content_hash = excluded.content_hash,
           tokenizer = excluded.tokenizer,
           ingestion_job_id = excluded.ingestion_job_id
"""

_LIST = """
    select id, workspace_id, document_id, chunk_index, text, token_count,
           page_number, page_numbers, start_offset, end_offset,
           source_start_offset, source_end_offset, content_hash
      from public.document_chunks
     where workspace_id = $1
       and document_id = $2
     order by chunk_index asc
"""

_COUNT = """
    select count(*) as total
      from public.document_chunks
     where workspace_id = $1
       and document_id = $2
"""


class ChunkRepository(Protocol):
    """Storage for the chunks a document produced."""

    async def replace_for_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        chunks: list[DocumentChunk],
        *,
        tokenizer: str,
        ingestion_job_id: UUID | None = None,
    ) -> int:
        """Make the document's stored chunks exactly ``chunks``.

        Idempotent: running it twice with the same input leaves the same rows,
        and running it with a changed document removes the chunks that no
        longer exist rather than leaving them behind as evidence nothing
        supports. Returns how many chunks the document now has.
        """
        ...

    async def list_for_document(self, workspace_id: UUID, document_id: UUID) -> list[DocumentChunk]:
        """The document's chunks, in reading order."""
        ...

    async def count_for_document(self, workspace_id: UUID, document_id: UUID) -> int: ...


def _to_chunk(record: asyncpg.Record) -> DocumentChunk:
    return DocumentChunk(
        chunk_id=record["id"],
        document_id=record["document_id"],
        workspace_id=record["workspace_id"],
        text=record["text"],
        chunk_index=record["chunk_index"],
        page_number=record["page_number"],
        page_numbers=tuple(record["page_numbers"] or ()),
        token_count=record["token_count"],
        start_offset=record["start_offset"],
        end_offset=record["end_offset"],
        source_start_offset=record["source_start_offset"],
        source_end_offset=record["source_end_offset"],
        content_hash=record["content_hash"],
    )


class PostgresChunkRepository:
    """:class:`ChunkRepository` backed by Supabase Postgres."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def replace_for_document(
        self,
        workspace_id: UUID,
        document_id: UUID,
        chunks: list[DocumentChunk],
        *,
        tokenizer: str,
        ingestion_job_id: UUID | None = None,
    ) -> int:
        # One transaction: a half-replaced document -- some chunks from the old
        # run, some from the new -- is a corpus that cites text the document no
        # longer contains.
        async with self._pool.acquire() as connection, connection.transaction():
            if not chunks:
                await connection.execute(_DELETE_ALL, document_id)
                return 0

            await connection.execute(
                _DELETE_ABSENT, document_id, [chunk.chunk_id for chunk in chunks]
            )
            await connection.executemany(
                _UPSERT,
                [
                    (
                        chunk.chunk_id,
                        workspace_id,
                        document_id,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.token_count,
                        chunk.page_number,
                        list(chunk.page_numbers),
                        chunk.start_offset,
                        chunk.end_offset,
                        chunk.source_start_offset,
                        chunk.source_end_offset,
                        chunk.content_hash,
                        tokenizer,
                        ingestion_job_id,
                    )
                    for chunk in chunks
                ],
            )
            return len(chunks)

    async def list_for_document(self, workspace_id: UUID, document_id: UUID) -> list[DocumentChunk]:
        records = await self._pool.fetch(_LIST, workspace_id, document_id)
        return [_to_chunk(record) for record in records]

    async def count_for_document(self, workspace_id: UUID, document_id: UUID) -> int:
        record = await self._pool.fetchrow(_COUNT, workspace_id, document_id)
        return 0 if record is None else int(record["total"])
