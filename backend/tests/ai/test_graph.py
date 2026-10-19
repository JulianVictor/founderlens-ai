"""Graph schema, Neo4j payload generation and idempotent indexing.

No Neo4j runs here. What is asserted is the Cypher and the parameters the
repository produces -- which is what a database would act on -- plus the
schema statements. That catches a payload missing ``workspace_id`` or a
statement that forgets to scope, which is the class of bug that matters most
on a store with no row-level security. It does not catch a Cypher syntax
error; only a live database can, and that is stated rather than implied.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from app.ai.graph import (
    CHUNK_VECTOR_INDEX,
    Neo4jChunkGraph,
    build_chunk_payload,
    schema_statements,
)
from app.ai.ingestion import DocumentChunk, content_hash
from app.core.exceptions import GraphError

WORKSPACE_ID = UUID("00000000-0000-4000-8000-0000000000a1")
OTHER_WORKSPACE_ID = UUID("00000000-0000-4000-8000-0000000000a2")
DOCUMENT_ID = UUID("00000000-0000-4000-8000-0000000000d1")


def make_chunk(index: int, text: str, *, page: int | None = 1) -> DocumentChunk:
    digest = content_hash(text)
    return DocumentChunk(
        chunk_id=UUID(int=index + 1000),
        document_id=DOCUMENT_ID,
        workspace_id=WORKSPACE_ID,
        text=text,
        chunk_index=index,
        page_number=page,
        page_numbers=() if page is None else (page,),
        token_count=len(text.split()),
        start_offset=index * 100,
        end_offset=index * 100 + len(text),
        source_start_offset=index * 100,
        source_end_offset=index * 100 + len(text),
        content_hash=digest,
    )


class RecordingGraphDatabase:
    """Stands in for :class:`Neo4jDatabase`, capturing every statement."""

    def __init__(self, *, fail: bool = False) -> None:
        self.writes: list[tuple[str, dict[str, Any]]] = []
        self.reads: list[tuple[str, dict[str, Any]]] = []
        self.read_result: list[dict[str, Any]] = []
        self._fail = fail

    async def execute_write(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        if self._fail:
            raise GraphError("write", "unreachable")
        self.writes.append((query, parameters or {}))
        return []

    async def execute_read(
        self, query: str, parameters: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        if self._fail:
            raise GraphError("read", "unreachable")
        self.reads.append((query, parameters or {}))
        return self.read_result


@pytest.fixture
def database() -> RecordingGraphDatabase:
    return RecordingGraphDatabase()


@pytest.fixture
def graph(database: RecordingGraphDatabase) -> Neo4jChunkGraph:
    return Neo4jChunkGraph(database)  # type: ignore[arg-type]


class TestSchema:
    def test_creates_a_vector_index_at_the_configured_width(self) -> None:
        statements = schema_statements(1536)
        vector = [s for s in statements if "VECTOR INDEX" in s]

        assert len(vector) == 1
        assert "1536" in vector[0]
        assert CHUNK_VECTOR_INDEX in vector[0]

    def test_the_vector_index_uses_cosine_similarity(self) -> None:
        """Matches how the embeddings are normalised and how retrieval will
        score; a mismatch here ranks plausibly and wrongly."""
        vector = next(s for s in schema_statements(8) if "VECTOR INDEX" in s)

        assert "cosine" in vector

    def test_every_statement_is_idempotent(self) -> None:
        """Applied on every boot, so a second run must not fail."""
        for statement in schema_statements(8):
            assert "IF NOT EXISTS" in statement

    def test_workspace_id_is_mandatory_on_both_labels(self) -> None:
        """A node without it is invisible to every scoped query -- and worse,
        a future query that forgot to scope would return it to everyone."""
        joined = "\n".join(schema_statements(8))

        assert "FOR (d:Document) REQUIRE d.workspace_id IS NOT NULL" in joined
        assert "FOR (c:Chunk) REQUIRE c.workspace_id IS NOT NULL" in joined

    def test_identity_is_constrained_on_both_labels(self) -> None:
        joined = "\n".join(schema_statements(8))

        assert "FOR (d:Document) REQUIRE d.id IS UNIQUE" in joined
        assert "FOR (c:Chunk) REQUIRE c.id IS UNIQUE" in joined

    def test_workspace_id_is_indexed(self) -> None:
        joined = "\n".join(schema_statements(8))

        assert "FOR (c:Chunk) ON (c.workspace_id)" in joined

    @pytest.mark.parametrize("bad", [0, -1, -1536])
    def test_a_non_positive_width_is_refused(self, bad: int) -> None:
        with pytest.raises(ValueError, match="positive"):
            schema_statements(bad)

    def test_a_non_integer_width_is_refused(self) -> None:
        """The width is formatted into DDL because Neo4j takes no parameters
        there, so it is checked rather than trusted."""
        with pytest.raises(TypeError):
            schema_statements("1536; DROP DATABASE neo4j")  # type: ignore[arg-type]

    def test_booleans_are_not_accepted_as_a_width(self) -> None:
        with pytest.raises(TypeError):
            schema_statements(True)

    async def test_applying_the_schema_runs_every_statement(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        await graph.apply_schema(8)

        assert len(database.writes) == len(schema_statements(8))


class TestChunkPayload:
    def test_carries_the_conceptual_model(self) -> None:
        chunk = make_chunk(0, "Acme raised twelve million dollars.")

        payload = build_chunk_payload(chunk, [0.1, 0.2])

        assert payload["id"] == str(chunk.chunk_id)
        assert payload["text"] == chunk.text
        assert payload["chunk_index"] == 0
        assert payload["page_number"] == 1
        assert payload["content_hash"] == chunk.content_hash
        assert payload["embedding"] == [0.1, 0.2]

    def test_identifiers_are_strings(self) -> None:
        """Neo4j has no UUID type. Strings keep them comparable with the
        Postgres ids they mirror."""
        payload = build_chunk_payload(make_chunk(0, "Acme."), [0.1])

        assert isinstance(payload["id"], str)

    def test_page_numbers_are_always_a_list(self) -> None:
        """One shape, so a query never has to handle two."""
        with_page = build_chunk_payload(make_chunk(0, "Acme.", page=3), [0.1])
        without = build_chunk_payload(make_chunk(0, "Acme.", page=None), [0.1])

        assert with_page["page_numbers"] == [3]
        assert without["page_numbers"] == []
        assert without["page_number"] is None

    def test_offsets_survive_into_the_graph(self) -> None:
        """A chunk node that cannot be resolved back to a span of the
        document cannot be cited."""
        payload = build_chunk_payload(make_chunk(2, "Acme raised."), [0.1])

        assert payload["start_offset"] == 200
        assert payload["source_start_offset"] == 200
        assert payload["end_offset"] > payload["start_offset"]


class TestIndexing:
    async def test_writes_the_document_and_its_chunks(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        chunks = [make_chunk(0, "First chunk."), make_chunk(1, "Second chunk.")]

        written = await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=chunks,
            embeddings=[[0.1], [0.2]],
            embedding_model="fake-model",
        )

        assert written == 2
        queries = " ".join(query for query, _ in database.writes)
        assert "MERGE (d:Document" in queries
        assert "MERGE (c:Chunk" in queries
        assert "MERGE (d)-[:HAS_CHUNK]->(c)" in queries

    async def test_every_statement_is_scoped_by_workspace(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        """Neo4j has no row-level security, so this is the whole of the
        isolation guarantee on this path."""
        await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=[make_chunk(0, "Acme.")],
            embeddings=[[0.1]],
            embedding_model="fake-model",
        )

        for query, parameters in database.writes:
            assert parameters["workspace_id"] == str(WORKSPACE_ID)
            assert "workspace_id" in query

    async def test_every_chunk_node_carries_its_workspace(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=[make_chunk(0, "Acme."), make_chunk(1, "Initech.")],
            embeddings=[[0.1], [0.2]],
            embedding_model="fake-model",
        )

        upsert = next(q for q, _ in database.writes if "MERGE (c:Chunk" in q)
        assert "c.workspace_id = $workspace_id" in upsert

    async def test_stale_chunks_are_removed_before_new_ones_are_written(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        """The other order leaves a window in which the document has both its
        old and new chunks, and a retrieval landing there cites text the
        document no longer contains.
        """
        await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=[make_chunk(0, "Acme.")],
            embeddings=[[0.1]],
            embedding_model="fake-model",
        )

        order = [query for query, _ in database.writes]
        delete_at = next(i for i, q in enumerate(order) if "DETACH DELETE" in q)
        upsert_at = next(i for i, q in enumerate(order) if "MERGE (c:Chunk" in q)
        assert delete_at < upsert_at

    async def test_the_delete_keeps_the_chunks_the_run_produced(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        chunks = [make_chunk(0, "Acme."), make_chunk(1, "Initech.")]

        await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=chunks,
            embeddings=[[0.1], [0.2]],
            embedding_model="fake-model",
        )

        _, parameters = next((q, p) for q, p in database.writes if "DETACH DELETE" in q)
        assert parameters["keep_ids"] == [str(c.chunk_id) for c in chunks]

    async def test_chunks_are_paired_with_their_own_vectors(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        chunks = [make_chunk(0, "First."), make_chunk(1, "Second.")]

        await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=chunks,
            embeddings=[[1.0], [2.0]],
            embedding_model="fake-model",
        )

        _, parameters = next((q, p) for q, p in database.writes if "UNWIND" in q)
        payloads = parameters["chunks"]
        assert payloads[0]["text"] == "First."
        assert payloads[0]["embedding"] == [1.0]
        assert payloads[1]["text"] == "Second."
        assert payloads[1]["embedding"] == [2.0]

    async def test_mismatched_counts_are_refused(self, graph: Neo4jChunkGraph) -> None:
        """Zipping lists of different lengths would attach each chunk to
        another chunk's vector -- plausible, wrong retrieval."""
        with pytest.raises(GraphError, match="2 chunks but 1 embeddings"):
            await graph.index_document(
                workspace_id=WORKSPACE_ID,
                document_id=DOCUMENT_ID,
                filename="acme.pdf",
                mime_type="application/pdf",
                chunks=[make_chunk(0, "a"), make_chunk(1, "b")],
                embeddings=[[0.1]],
                embedding_model="fake-model",
            )

    async def test_a_document_with_no_chunks_clears_its_chunks(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        written = await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=[],
            embeddings=[],
            embedding_model="fake-model",
        )

        assert written == 0
        assert any("DETACH DELETE" in q for q, _ in database.writes)

    async def test_the_embedding_model_is_recorded_on_the_nodes(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        """Vectors from different models are not comparable, so a stored
        vector without this is a number nobody can safely use."""
        await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=[make_chunk(0, "Acme.")],
            embeddings=[[0.1]],
            embedding_model="text-embedding-3-small",
        )

        query, parameters = next((q, p) for q, p in database.writes if "UNWIND" in q)
        assert "c.embedding_model = $embedding_model" in query
        assert parameters["embedding_model"] == "text-embedding-3-small"


class TestIdempotentIndexing:
    async def test_merges_rather_than_creates(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        """CREATE would add a rival node on every run; MERGE on the
        deterministic id updates the one that is already there."""
        await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=[make_chunk(0, "Acme.")],
            embeddings=[[0.1]],
            embedding_model="fake-model",
        )

        for query, _ in database.writes:
            assert "CREATE (c:Chunk" not in query
            assert "CREATE (d:Document" not in query

    async def test_the_relationship_is_merged_too(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        """Otherwise a second run adds a second HAS_CHUNK edge between the
        same two nodes."""
        await graph.index_document(
            workspace_id=WORKSPACE_ID,
            document_id=DOCUMENT_ID,
            filename="acme.pdf",
            mime_type="application/pdf",
            chunks=[make_chunk(0, "Acme.")],
            embeddings=[[0.1]],
            embedding_model="fake-model",
        )

        upsert = next(q for q, _ in database.writes if "UNWIND" in q)
        assert "MERGE (d)-[:HAS_CHUNK]->(c)" in upsert
        assert "CREATE (d)-[:HAS_CHUNK]->(c)" not in upsert

    async def test_the_same_input_produces_the_same_parameters(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        """Re-indexing unchanged content must send the database the same
        thing, or "idempotent" depends on Neo4j tolerating a difference."""
        chunks = [make_chunk(0, "Acme."), make_chunk(1, "Initech.")]
        arguments = {
            "workspace_id": WORKSPACE_ID,
            "document_id": DOCUMENT_ID,
            "filename": "acme.pdf",
            "mime_type": "application/pdf",
            "chunks": chunks,
            "embeddings": [[0.1], [0.2]],
            "embedding_model": "fake-model",
        }

        await graph.index_document(**arguments)  # type: ignore[arg-type]
        first = list(database.writes)
        database.writes.clear()
        await graph.index_document(**arguments)  # type: ignore[arg-type]

        assert database.writes == first


class TestDeletionAndCounting:
    async def test_deleting_a_document_is_workspace_scoped(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        """A guessed document id from another workspace must not delete it."""
        await graph.delete_document(OTHER_WORKSPACE_ID, DOCUMENT_ID)

        query, parameters = database.writes[0]
        assert "workspace_id: $workspace_id" in query
        assert parameters["workspace_id"] == str(OTHER_WORKSPACE_ID)

    async def test_counting_uses_a_read_transaction(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        """Read paths use read transactions so a mistake in a template cannot
        mutate the graph."""
        database.read_result = [{"total": 3}]

        assert await graph.count_chunks(WORKSPACE_ID, DOCUMENT_ID) == 3
        assert database.writes == []
        assert len(database.reads) == 1

    async def test_counting_an_unknown_document_is_zero(
        self, graph: Neo4jChunkGraph, database: RecordingGraphDatabase
    ) -> None:
        database.read_result = []

        assert await graph.count_chunks(WORKSPACE_ID, DOCUMENT_ID) == 0

    async def test_a_graph_failure_propagates(self) -> None:
        failing = RecordingGraphDatabase(fail=True)
        graph = Neo4jChunkGraph(failing)  # type: ignore[arg-type]

        with pytest.raises(GraphError):
            await graph.index_document(
                workspace_id=WORKSPACE_ID,
                document_id=DOCUMENT_ID,
                filename="acme.pdf",
                mime_type="application/pdf",
                chunks=[make_chunk(0, "Acme.")],
                embeddings=[[0.1]],
                embedding_model="fake-model",
            )
