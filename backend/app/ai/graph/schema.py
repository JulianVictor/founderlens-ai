"""Neo4j constraints and indexes.

Every statement is ``IF NOT EXISTS``, so applying the schema is idempotent and
can run on every startup. Neo4j has no migration tool of its own; this module
is the equivalent, and it is deliberately declarative rather than versioned
because the graph is derived data that can be rebuilt.

Why uniqueness is on ``id``
---------------------------
``data-model.md`` specifies a composed ``uid`` of ``workspace:entity_id`` for
resolved entities, because the entity-resolution pipeline mints those ids and
two workspaces could independently produce the same one. ``:Document`` and
``:Chunk`` are different: their ids are UUIDs minted by Postgres, already
unique across every workspace, so composing a key would add a property that
could only ever restate what the UUID already guarantees. The ``uid`` scheme
still applies to Company, Person and the rest when they arrive.
"""

from __future__ import annotations

from typing import Final

#: Name of the vector index, referenced by retrieval later.
CHUNK_VECTOR_INDEX: Final = "chunk_embedding_index"
CHUNK_FULLTEXT_INDEX: Final = "chunk_text_index"

#: Statements with no parameters. Neo4j does not accept parameters in DDL, so
#: anything variable -- the vector dimension -- is formatted in separately and
#: validated before it gets there.
_STATEMENTS: Final[tuple[str, ...]] = (
    # Identity. Also the lookup the chunk upsert MERGEs on, and a MERGE on an
    # unindexed property is a full label scan.
    """
    CREATE CONSTRAINT document_id_unique IF NOT EXISTS
    FOR (d:Document) REQUIRE d.id IS UNIQUE
    """,
    """
    CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS
    FOR (c:Chunk) REQUIRE c.id IS UNIQUE
    """,
    # workspace_id is mandatory, not merely conventional. A node without it
    # would be invisible to every workspace-scoped query -- and, worse, a
    # future query that forgot to filter would return it to everyone.
    """
    CREATE CONSTRAINT document_workspace_exists IF NOT EXISTS
    FOR (d:Document) REQUIRE d.workspace_id IS NOT NULL
    """,
    """
    CREATE CONSTRAINT chunk_workspace_exists IF NOT EXISTS
    FOR (c:Chunk) REQUIRE c.workspace_id IS NOT NULL
    """,
    # workspace_id is a predicate on essentially every query, so it is indexed
    # on both labels.
    """
    CREATE INDEX document_workspace_idx IF NOT EXISTS
    FOR (d:Document) ON (d.workspace_id)
    """,
    """
    CREATE INDEX chunk_workspace_idx IF NOT EXISTS
    FOR (c:Chunk) ON (c.workspace_id)
    """,
    # "This document's chunks", which is what re-indexing and deletion walk.
    """
    CREATE INDEX chunk_document_idx IF NOT EXISTS
    FOR (c:Chunk) ON (c.document_id)
    """,
    # Finding repeated content across a workspace without comparing text.
    """
    CREATE INDEX chunk_content_hash_idx IF NOT EXISTS
    FOR (c:Chunk) ON (c.workspace_id, c.content_hash)
    """,
)

_VECTOR_INDEX: Final = """
CREATE VECTOR INDEX {name} IF NOT EXISTS
FOR (c:Chunk) ON (c.embedding)
OPTIONS {{ indexConfig: {{
    `vector.dimensions`: {dimensions},
    `vector.similarity_function`: 'cosine'
}} }}
"""

_FULLTEXT_INDEX: Final = """
CREATE FULLTEXT INDEX {name} IF NOT EXISTS
FOR (c:Chunk) ON EACH [c.text]
"""


def schema_statements(dimensions: int) -> list[str]:
    """Every statement needed to bring the graph schema up to date.

    ``dimensions`` is formatted into the vector index because Neo4j does not
    accept parameters in DDL. It is validated as a positive integer first --
    it comes from configuration, and string-formatting an unchecked value into
    a statement is how injection happens even when the source seems trusted.
    """
    if not isinstance(dimensions, int) or isinstance(dimensions, bool):
        raise TypeError("dimensions must be an int")
    if dimensions <= 0:
        raise ValueError("dimensions must be positive")

    return [
        *(statement.strip() for statement in _STATEMENTS),
        _VECTOR_INDEX.format(name=CHUNK_VECTOR_INDEX, dimensions=dimensions).strip(),
        _FULLTEXT_INDEX.format(name=CHUNK_FULLTEXT_INDEX).strip(),
    ]
