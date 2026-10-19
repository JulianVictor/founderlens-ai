"""Neo4j access: the knowledge graph and the chunk vector index.

All Cypher lives here, as fixed templates with bound parameters. Two rules the
rest of the system depends on:

* **Every statement is scoped by workspace.** Neo4j has no row-level security,
  so isolation is application-enforced, and keeping the statements in one
  short list is what makes "does every query filter on workspace_id" a
  question somebody can actually answer.
* **Nothing assembles Cypher from input.** Not from a caller, and later not
  from a model. A future retriever picks a template and supplies parameters.
"""

from app.ai.graph.chunk_graph import (
    ChunkGraph,
    Neo4jChunkGraph,
    build_chunk_payload,
)
from app.ai.graph.schema import (
    CHUNK_FULLTEXT_INDEX,
    CHUNK_VECTOR_INDEX,
    schema_statements,
)

__all__ = [
    "CHUNK_FULLTEXT_INDEX",
    "CHUNK_VECTOR_INDEX",
    "ChunkGraph",
    "Neo4jChunkGraph",
    "build_chunk_payload",
    "schema_statements",
]
