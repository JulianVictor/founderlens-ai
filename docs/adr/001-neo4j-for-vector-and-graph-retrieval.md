# ADR 001 — Neo4j for both vector and graph retrieval

- **Status:** Accepted
- **Date:** 2026-09-11
- **Context:** Phase 0, before any retrieval code exists
- **Supersedes:** none

## Context

FounderLens needs two kinds of retrieval over a workspace's documents:

1. **Semantic similarity** — find the chunks whose meaning is closest to a
   question. Requires an approximate nearest-neighbour index over embeddings.
2. **Relationship traversal** — find facts several hops from the question's
   subject: *investors in companies that compete with X*. Requires a graph.

Both must return **chunks**, because a chunk is the unit of citation: an answer
is only checkable if every claim resolves to a span of source text. A graph edge
that cannot produce the sentence that justifies it is not usable evidence.

The obvious architecture is one store per job: a vector database for (1) and
Neo4j for (2). This record explains why we are not doing that yet.

## Decision

**Neo4j stores both the chunk vectors and the knowledge graph.** Embeddings live
as a property on the `:Chunk` node, indexed by Neo4j's native vector index. We
do not run a second vector database.

## Rationale

**The vectors and the graph are attached to the same nodes.** `:Chunk` already
has to exist in the graph — `(:Document)-[:HAS_CHUNK]->(:Chunk)` and
`(:Chunk)-[:MENTIONS]->(entity)` are how provenance works. Putting the embedding
on that node means the thing vector search returns *is* the thing graph
traversal walks through. A separate vector store would return an identifier we
would then have to look up in Neo4j on every single query.

**It removes a distributed consistency problem we would otherwise own.** With
two stores, writing a chunk is two writes across two systems with no shared
transaction. Every partial failure produces a chunk that is searchable but not
traceable, or traceable but not searchable — both of which are silent
corruptions of grounding rather than loud errors. Reconciliation between the two
would become permanent, load-bearing infrastructure. One store makes this
failure mode not exist.

**Evidence merge gets simpler and more correct.** Vector hits and graph hits
arrive as the same node type, with the same properties and the same provenance,
so deduplicating between them is an identity check rather than a mapping
exercise. When a chunk is found by both retrievers — a strong signal — that is
detectable directly.

**One store means one place to enforce workspace isolation.** Neo4j has no
row-level security, so isolation is enforced in application code. That is a risk
worth minimising by having exactly one such surface. Two stores would mean two
independent scoping mechanisms, and a cross-workspace leak needs only one of
them to be wrong.

**It is one fewer system to operate.** No second service to run locally, deploy,
back up, secure, budget for, or keep schema-synchronised — at a stage where
there is no retrieval code at all and the corpus is small.

**Native support exists and the ecosystem assumes it.** Neo4j 5 ships a vector
index with cosine similarity, and `neo4j-graphrag` — which CLAUDE.md names as
the intended library — is built around exactly this arrangement.

**The scale argument does not apply yet.** Dedicated vector databases earn their
place at a scale FounderLens is nowhere near. Adopting one now would be paying
its operational cost to solve a problem we do not have, while creating a
consistency problem we would have immediately.

## Alternatives considered

### pgvector in Supabase Postgres, graph in Neo4j

Attractive because Postgres is already a dependency, so it adds no new service,
and it would give transactional writes between document metadata and chunk
vectors.

Rejected because it splits the chunk itself. The chunk row would live in
Postgres while the `:Chunk` node lived in Neo4j, so every multi-hop query would
cross stores: traverse in Neo4j, collect chunk ids, fetch text from Postgres,
merge in application code. That is the distributed consistency problem above,
plus a per-query join across a network boundary, in exchange for transactional
guarantees on derived data that we are willing to rebuild anyway.

This is the strongest alternative and the most likely successor if Neo4j's
vector search disappoints — it keeps the service count unchanged.

### A dedicated vector database (Pinecone, Qdrant, Weaviate)

Better ANN performance, richer filtering, and hybrid search as a first-class
feature.

Rejected for now: it adds a third data store and a second workspace-isolation
surface, keeps the split-chunk problem, and its advantages are all at a scale we
have not reached. Revisit when the triggers below fire.

### Graph in Postgres, dropping Neo4j

Recursive CTEs can traverse. Rejected: multi-hop traversal over a
heterogeneous entity graph is exactly what a graph database is for, the
queries become unreadable at three hops, and the knowledge graph is a headline
capability of this system rather than an incidental feature.

## Consequences

### Positive

- One write path and one provenance model for chunks.
- Vector and graph results are directly comparable and trivially deduplicated.
- One workspace-isolation surface to audit.
- Fewer services to run; faster local setup.
- Straightforward fit with `neo4j-graphrag`.

### Negative, and accepted

- **Vector filtering is the sharp edge.** A nearest-neighbour query returns the
  global top *k*, so workspace filtering happens *after* the index lookup. We
  over-fetch and then filter, and must assert that enough results survive.
  With many workspaces in one database, over-fetch cost grows. Mitigations, in
  order of preference: tune the over-fetch factor against real data; use index
  metadata filtering where the Neo4j version supports it; partition by database
  per workspace. **This needs benchmarking before Phase 2 rather than
  assumption** — it is the most likely reason this ADR gets superseded.
- **ANN tuning is more limited** than in a dedicated vector database, and index
  rebuilds after a re-embed are a whole-index operation.
- **Hybrid search must be assembled** from the vector index and a full-text
  index with our own fusion, rather than being provided.
- **One store is one blast radius.** Heavy vector search and heavy traversal
  compete for the same resources.
- **Embedding dimension is baked into the index.** Changing
  `EMBEDDING_MODEL`/`EMBEDDING_DIMENSIONS` requires re-embedding every chunk and
  rebuilding the index. This is why both values are recorded on every
  `IngestionJob` — see [`../data-model.md`](../data-model.md).

### Neutral

- The retrieval interface hides this choice. `vector_retrieval` returns ranked
  chunks; nothing upstream knows which store served them. Moving vectors
  elsewhere later is a change behind that interface, not a change to the
  pipeline.

## Revisit when

Any one of these should reopen the decision:

- p95 vector retrieval latency exceeds the answer latency budget;
- over-fetch-and-filter needs a factor large enough that it dominates query
  cost;
- corpus size makes index rebuild time operationally painful;
- hybrid retrieval quality plateaus in evaluation and the ceiling traces to
  index capability rather than to chunking or ranking;
- multi-tenancy grows to where per-workspace partitioning is needed anyway.

Because the retrieval interface isolates the choice, the migration would be
adding a vector store behind `vector_retrieval` and re-embedding — not a rewrite.
