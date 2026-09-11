# Architecture

FounderLens answers startup research questions from a workspace's own
documents, and makes every answer checkable: each claim points back at the text
that supports it, and unsupported questions return an explicit
insufficient-evidence result rather than a plausible guess.

This document covers the technology choices, the two pipelines, and the
invariants that hold across every phase. See also
[`data-model.md`](data-model.md), [`ai-pipeline.md`](ai-pipeline.md) and the
decision records in [`adr/`](adr/README.md).

## Technology choices

| Concern | Technology | Notes |
| --- | --- | --- |
| Frontend | Next.js (App Router), TypeScript strict, Tailwind | Server components by default; only `NEXT_PUBLIC_*` reaches the browser. |
| Backend | FastAPI, Python 3.12, Pydantic v2 | Layered; route handlers stay thin. |
| Relational / application data | Supabase PostgreSQL | System of record for workspaces, documents, jobs and runs. |
| Authentication | Supabase Auth | Issues the JWT the API verifies; `auth.users` is the identity table. |
| Document storage | Supabase Storage | Holds the uploaded bytes; Postgres holds the metadata pointing at them. |
| AI retrieval | Neo4j | Both the chunk vector index and the knowledge graph — see [ADR 001](adr/001-neo4j-for-vector-and-graph-retrieval.md). |
| LLM and embeddings | Configurable providers | Selected by `LLM_PROVIDER` / `EMBEDDING_PROVIDER`; interfaces from day one. |

Supabase supplies three distinct services (Auth, Postgres, Storage) that happen
to share a vendor. They are treated as three dependencies, not one, so that any
of them can be replaced without the others moving.

## Why application state is separated from graph retrieval data

Supabase Postgres is the **system of record**. Neo4j is a **derived retrieval
index**. This split is deliberate, and the reasons are worth stating because the
alternative — one store for everything — is superficially simpler.

**They answer different questions.** Application state answers *"who owns this
workspace, which documents are in it, did ingestion job 47 fail, and what did we
answer last Tuesday?"* Those are transactional, row-oriented, constraint-heavy
questions that a relational database is built for. Retrieval answers *"which
passages are semantically near this question, and which companies sit within two
hops of this investor?"* Those are similarity and traversal questions, where a
graph with a vector index is the right shape and a relational schema is not.

**Their lifecycles differ.** Application state is authoritative and must survive
everything. Graph and vector data are *derived* from documents by a pipeline
that will change — better chunking, a new embedding model, an improved
extraction prompt. When that happens we want to drop a workspace's graph and
rebuild it from the documents and the job history. That is a safe operation
precisely because the graph is not the source of truth. Mixing the two would
turn every pipeline improvement into a data migration against authoritative
data.

**Their consistency requirements differ.** Membership changes and ownership
checks need transactional integrity. A knowledge graph assembled by an LLM is
probabilistic: entities carry confidence scores, and relationships are
assertions extracted from text with a provenance trail rather than facts.
Storing a confidence-weighted, revisable assertion next to a
foreign-key-enforced membership row invites treating one like the other.

**Their access control differs.** Postgres enforces isolation declaratively with
row-level security tied to the authenticated user. Neo4j has no equivalent, so
workspace isolation there is enforced in application code — every node and
relationship carries `workspace_id`, and every query template filters on it.
Keeping the two apart keeps that difference visible instead of blurring it.

**The seam.** `Document` deliberately exists on both sides: the Postgres row is
authoritative (bytes, checksum, status, uploader), and the Neo4j `:Document`
node is a lightweight projection carrying the same UUID so that chunks and
entities can be traced back to it. The UUID is minted by Postgres. Neo4j never
invents an identifier that Postgres does not already know.

```
            +------------- Supabase --------------+
 browser -->|  Auth  |  PostgreSQL  |  Storage    |   system of record
            +---------------+---------------------+
                            |  document id, bytes
                            v
                    ingestion pipeline               derived, rebuildable
                            |
                            v
                    +----------------+
                    |     Neo4j      |  chunk vectors + knowledge graph
                    +----------------+
```

## Backend layering

Dependencies point in one direction only:

```
api/           HTTP transport. Thin handlers, no domain logic.
  |
  v
services/      Orchestration: use cases, transaction boundaries.
  |
  v
repositories/  All I/O against Postgres, Storage and Neo4j.
models/        Persistence shapes (rows, nodes, relationships).
schemas/       Pydantic contracts crossing the API boundary.
core/          Config, logging, shared primitives.
ai/            The research pipeline (see below).
```

A route handler resolves its dependencies, calls one service and serialises the
result. Anything that looks like a decision belongs in `services/` or `ai/`.

## Ingestion pipeline

From an uploaded file to a queryable graph. Each stage is a step over the
previous stage's output, so any stage can be re-run from stored intermediates.

```
document
   -> extraction         raw bytes to text, with page and offset information
   -> cleaning           normalise, de-noise, drop boilerplate
   -> chunking           split into retrievable units with character spans
   -> embeddings         vectorise each chunk
   -> entity extraction  structured entities and relationships per chunk
   -> entity resolution  map mentions onto canonical workspace entities
   -> graph persistence  write nodes, relationships and vectors to Neo4j
```

> **A note on naming.** The *extraction* stage means **text** extraction —
> getting characters out of a PDF or DOCX. The later *entity extraction* stage
> is the LLM step that finds companies, people and funding rounds. They are
> different operations and they live in different packages:
> text extraction, cleaning and chunking are all `app/ai/ingestion/`, while
> entity extraction is `app/ai/extraction/`. Where a pipeline listing says
> "extraction" unqualified, it means text extraction.

Stages run inside a job tracked by an `IngestionJob` row, which records the
furthest stage reached, so a failure is diagnosable and resumable rather than an
opaque "ingestion failed".

## Query pipeline

From a question to a grounded, cited answer.

```
question
   -> query analysis       intent, named entities, time bounds, decomposition
   -> retrieval planning   choose vector, graph or both; set budgets
   -> vector retrieval     semantic search over the workspace's chunk vectors
   -> graph retrieval      constrained traversal from anchor entities
   -> evidence merge       deduplicate, rank and trim to a context budget
   -> answer generation    compose an answer over the merged evidence only
   -> citation validation  verify every claim maps to cited evidence
```

Citation validation is a gate, not a formatting step. If a claim cannot be tied
to retrieved evidence, the claim is not emitted; if the answer as a whole is
unsupported, the run returns `insufficient_evidence`. The full trace — plan,
retrieved chunks, scores, graph paths — is persisted on the `ResearchRun` so
that an answer can be audited after the fact.

## Invariants

These hold for every phase of the project:

- **Workspace isolation.** Every query — SQL, vector or Cypher — is scoped to a
  `workspace_id` whose ownership has been verified for the caller.
- **Provenance.** Every chunk, entity and relationship records the document and
  span it came from.
- **Grounding.** A claim without supporting retrieved evidence is not emitted;
  the generator returns an explicit insufficient-evidence result instead.
- **Entity identity is ours.** LLM-proposed IDs are never trusted; identity is
  assigned by the entity-resolution pipeline.
- **No free-form Cypher.** The LLM never emits Cypher that is executed directly.
  Graph access goes through constrained templates or validated read-only queries.
- **Derived data is rebuildable.** Dropping and rebuilding a workspace's graph
  from its documents is a supported, routine operation.

## Provider abstraction

LLM and embedding providers sit behind interfaces from the start, because both
are genuinely swappable and selected by configuration. Nothing else is
abstracted until a second real implementation exists.

Because embeddings are model-specific, the embedding model and its dimension
(`EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`) are recorded on every ingestion job
and research run. Changing the model invalidates existing vectors and requires a
re-embed; the recorded value is what makes that detectable rather than silent.
