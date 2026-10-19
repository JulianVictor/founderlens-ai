"""Writing documents and chunks into Neo4j.

Every statement is a fixed template with bound parameters. Nothing assembles
Cypher from a caller's input, and nothing will later assemble it from a
model's output -- that rule is easier to keep when the templates are a short
list in one module than when they are scattered across call sites.

**Workspace isolation is enforced here, in code.** Neo4j has no row-level
security, so every node written carries ``workspace_id`` and every statement
that reads or deletes filters on it. The constraint in
:mod:`app.ai.graph.schema` makes the property mandatory so a node cannot exist
outside a workspace.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Protocol
from uuid import UUID

from app.ai.embeddings.provider import Embedding
from app.ai.graph.schema import schema_statements
from app.ai.ingestion.models import DocumentChunk
from app.core.exceptions import GraphError
from app.core.neo4j import Neo4jDatabase

logger: Final = logging.getLogger(__name__)

_UPSERT_DOCUMENT: Final = """
MERGE (d:Document {id: $document_id})
SET d.workspace_id = $workspace_id,
    d.filename = $filename,
    d.mime_type = $mime_type,
    d.updated_at = datetime()
RETURN d.id AS id
"""

# Stale chunks go first, and the match is scoped by workspace as well as by
# document. Deleting after the upsert would leave a window in which the
# document had both its old and new chunks, and a retrieval landing there
# would cite text the document no longer contains.
_DELETE_STALE_CHUNKS: Final = """
MATCH (d:Document {id: $document_id, workspace_id: $workspace_id})-[:HAS_CHUNK]->(c:Chunk)
WHERE NOT c.id IN $keep_ids
DETACH DELETE c
"""

_DELETE_ALL_CHUNKS: Final = """
MATCH (d:Document {id: $document_id, workspace_id: $workspace_id})-[:HAS_CHUNK]->(c:Chunk)
DETACH DELETE c
"""

# MERGE on the deterministic id, so re-indexing an unchanged document updates
# the same nodes rather than creating rivals. MERGE on the relationship for the
# same reason: a second run must not add a second HAS_CHUNK edge.
_UPSERT_CHUNKS: Final = """
MATCH (d:Document {id: $document_id, workspace_id: $workspace_id})
UNWIND $chunks AS chunk
MERGE (c:Chunk {id: chunk.id})
SET c.workspace_id = $workspace_id,
    c.document_id = $document_id,
    c.text = chunk.text,
    c.chunk_index = chunk.chunk_index,
    c.page_number = chunk.page_number,
    c.page_numbers = chunk.page_numbers,
    c.token_count = chunk.token_count,
    c.start_offset = chunk.start_offset,
    c.end_offset = chunk.end_offset,
    c.source_start_offset = chunk.source_start_offset,
    c.source_end_offset = chunk.source_end_offset,
    c.content_hash = chunk.content_hash,
    c.embedding_model = $embedding_model,
    c.embedding = chunk.embedding,
    c.updated_at = datetime()
MERGE (d)-[:HAS_CHUNK]->(c)
RETURN count(c) AS written
"""

_DELETE_DOCUMENT: Final = """
MATCH (d:Document {id: $document_id, workspace_id: $workspace_id})
OPTIONAL MATCH (d)-[:HAS_CHUNK]->(c:Chunk)
DETACH DELETE c, d
"""

_COUNT_CHUNKS: Final = """
MATCH (:Document {id: $document_id, workspace_id: $workspace_id})-[:HAS_CHUNK]->(c:Chunk)
RETURN count(c) AS total
"""


class ChunkGraph(Protocol):
    """Writes a document and its chunks into the graph."""

    async def apply_schema(self, dimensions: int) -> None:
        """Create constraints and indexes if they are absent."""
        ...

    async def index_document(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        filename: str,
        mime_type: str,
        chunks: list[DocumentChunk],
        embeddings: list[Embedding],
        embedding_model: str,
    ) -> int:
        """Make the document's graph representation exactly these chunks.

        A replace, not an append: chunks the new run did not produce are
        removed. Returns how many chunks the document now has.
        """
        ...

    async def delete_document(self, workspace_id: UUID, document_id: UUID) -> None:
        """Remove the document and its chunks."""
        ...

    async def count_chunks(self, workspace_id: UUID, document_id: UUID) -> int: ...


def build_chunk_payload(chunk: DocumentChunk, embedding: Embedding) -> dict[str, Any]:
    """The property map for one ``:Chunk`` node.

    Kept as a named function rather than inlined into the query call so the
    shape written to the graph can be asserted in a test without a database.

    UUIDs become strings because Neo4j has no UUID type; storing them as
    strings keeps them comparable with the Postgres ids they mirror. Page
    numbers are a list even when there is one, so a query never has to handle
    two shapes.
    """
    return {
        "id": str(chunk.chunk_id),
        "text": chunk.text,
        "chunk_index": chunk.chunk_index,
        "page_number": chunk.page_number,
        "page_numbers": list(chunk.page_numbers),
        "token_count": chunk.token_count,
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "source_start_offset": chunk.source_start_offset,
        "source_end_offset": chunk.source_end_offset,
        "content_hash": chunk.content_hash,
        "embedding": embedding,
    }


class Neo4jChunkGraph:
    """:class:`ChunkGraph` backed by Neo4j."""

    def __init__(self, database: Neo4jDatabase) -> None:
        self._database = database

    async def apply_schema(self, dimensions: int) -> None:
        for statement in schema_statements(dimensions):
            await self._database.execute_write(statement)
        logger.info("Neo4j schema applied (vector dimensions: %d)", dimensions)

    async def index_document(
        self,
        *,
        workspace_id: UUID,
        document_id: UUID,
        filename: str,
        mime_type: str,
        chunks: list[DocumentChunk],
        embeddings: list[Embedding],
        embedding_model: str,
    ) -> int:
        if len(chunks) != len(embeddings):
            # Pairing by position only works if the lists are the same length.
            # Zipping mismatched ones would attach each chunk to another
            # chunk's vector, which retrieves plausibly and wrongly.
            raise GraphError(
                "index",
                f"{len(chunks)} chunks but {len(embeddings)} embeddings",
            )

        scope = {"workspace_id": str(workspace_id), "document_id": str(document_id)}

        await self._database.execute_write(
            _UPSERT_DOCUMENT,
            {**scope, "filename": filename, "mime_type": mime_type},
        )

        if not chunks:
            await self._database.execute_write(_DELETE_ALL_CHUNKS, scope)
            return 0

        payloads = [
            build_chunk_payload(chunk, embedding)
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ]

        await self._database.execute_write(
            _DELETE_STALE_CHUNKS,
            {**scope, "keep_ids": [payload["id"] for payload in payloads]},
        )
        await self._database.execute_write(
            _UPSERT_CHUNKS,
            {**scope, "chunks": payloads, "embedding_model": embedding_model},
        )
        return len(payloads)

    async def delete_document(self, workspace_id: UUID, document_id: UUID) -> None:
        await self._database.execute_write(
            _DELETE_DOCUMENT,
            {"workspace_id": str(workspace_id), "document_id": str(document_id)},
        )

    async def count_chunks(self, workspace_id: UUID, document_id: UUID) -> int:
        rows = await self._database.execute_read(
            _COUNT_CHUNKS,
            {"workspace_id": str(workspace_id), "document_id": str(document_id)},
        )
        return int(rows[0]["total"]) if rows else 0
