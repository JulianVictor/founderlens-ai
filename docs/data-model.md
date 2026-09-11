# Data model

Two stores, two models, one rule connecting them: Postgres mints identity,
Neo4j references it. See [`architecture.md`](architecture.md#why-application-state-is-separated-from-graph-retrieval-data)
for why they are separate.

This document defines the *initial* model for Phase 1. Columns and properties
will grow; the identity, isolation and provenance rules below are meant to hold.

## Universal rules

**`workspace_id` is on everything.** Every relational table and every graph node
and relationship carries `workspace_id` — including rows where it could be
derived through a join. The redundancy is deliberate:

- a row-level security policy can be written against the row itself, with no
  join and therefore no way to get the join wrong;
- every Cypher template filters on a property that is present on the pattern it
  is matching, so isolation does not depend on traversal reaching a parent node;
- a mis-scoped query is a bug you can find by grepping for a missing predicate.

**Provenance is on everything derived.** Any row, node or relationship produced
from a document records where it came from: the document, the chunks, the
pipeline run, and a confidence where the producer was an LLM. A graph assertion
whose provenance cannot be resolved back to text is a bug, and the
citation-validation gate treats it as one.

**Identifiers are UUIDs minted by Postgres.** Never by the LLM. Graph nodes that
mirror relational rows reuse the relational UUID; canonical entities that exist
only in the graph get their UUID from the entity-resolution pipeline.

**Soft delete at the top.** Deleting a workspace sets `deleted_at` and schedules
purge of its Storage objects and its graph partition. Graph data is rebuildable,
so purging it is not destructive of anything authoritative.

## Relational model (Supabase PostgreSQL)

Identity comes from Supabase Auth; `user_id` always references `auth.users(id)`.

### Workspace

The isolation boundary. Everything else hangs off it.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | |
| `name` | `text` | |
| `slug` | `text` | Unique; used in URLs. |
| `owner_id` | `uuid` | `auth.users(id)`. The one member who cannot be removed. |
| `created_at` / `updated_at` | `timestamptz` | |
| `deleted_at` | `timestamptz` NULL | Soft delete. |

### WorkspaceMember

Who may see a workspace, and at what level. This table is the sole authority
for access; no other check may substitute for it.

| Column | Type | Notes |
| --- | --- | --- |
| `workspace_id` | `uuid` PK part | |
| `user_id` | `uuid` PK part | `auth.users(id)`. |
| `role` | `text` | `owner` \| `admin` \| `member` \| `viewer`. |
| `invited_by` | `uuid` NULL | |
| `created_at` | `timestamptz` | |

Primary key `(workspace_id, user_id)`. `viewer` may ask questions and read
answers but may not upload or delete documents.

### Document

Metadata for one uploaded file. The bytes live in Supabase Storage; this row
points at them and is authoritative for everything about them.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | Reused as the `:Document` node id in Neo4j. |
| `workspace_id` | `uuid` | |
| `title` | `text` | |
| `source_type` | `text` | `upload` \| `url` \| `paste`. |
| `source_uri` | `text` NULL | Original URL, when there was one. |
| `storage_path` | `text` | Supabase Storage object key. |
| `content_type` | `text` | |
| `byte_size` | `bigint` | |
| `checksum_sha256` | `text` | Provenance anchor and dedup key. |
| `status` | `text` | `pending` \| `ingesting` \| `ready` \| `failed`. |
| `uploaded_by` | `uuid` | |
| `created_at` | `timestamptz` | |

Unique on `(workspace_id, checksum_sha256)`: re-uploading the same bytes into a
workspace resolves to the existing document instead of duplicating the corpus.

### IngestionJob

One run of the ingestion pipeline over one document. Exists so that a failure is
diagnosable and resumable rather than an opaque status flag.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | |
| `workspace_id` | `uuid` | |
| `document_id` | `uuid` | |
| `status` | `text` | `queued` \| `running` \| `succeeded` \| `failed` \| `cancelled`. |
| `stage` | `text` | Furthest stage reached: `extraction` … `graph_persistence`. |
| `attempt` | `int` | |
| `error_code` / `error_message` | `text` NULL | Populated on failure. |
| `pipeline_version` | `text` | Which pipeline produced this data. |
| `embedding_model` | `text` | Which vectors this job wrote. |
| `metrics` | `jsonb` | Chunk, token, entity and relationship counts; timings. |
| `started_at` / `finished_at` | `timestamptz` NULL | |

`pipeline_version` and `embedding_model` are what make derived data auditable:
they answer "which code and which model produced the graph I am querying", and
therefore "what needs re-running after this change".

### ResearchQuery

A question as asked. Separated from its runs because the same question can be
re-run against a corpus that has grown, and comparing those runs is the point.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | |
| `workspace_id` | `uuid` | |
| `question` | `text` | Verbatim, as typed. |
| `asked_by` | `uuid` | |
| `created_at` | `timestamptz` | |

### ResearchRun

One execution of the query pipeline. Holds the answer and the trace behind it.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | |
| `workspace_id` | `uuid` | |
| `query_id` | `uuid` | |
| `status` | `text` | `running` \| `completed` \| `failed`. |
| `outcome` | `text` | `answered` \| `insufficient_evidence`. |
| `answer_text` | `text` NULL | Null when `insufficient_evidence`. |
| `retrieval_plan` | `jsonb` | What the planner decided, and why. |
| `trace` | `jsonb` | Retrieved chunk ids, scores, graph paths, timings. |
| `llm_model` / `embedding_model` | `text` | Reproducibility. |
| `token_usage` | `jsonb` | |
| `latency_ms` | `int` | |
| `created_at` | `timestamptz` | |

`outcome = insufficient_evidence` is a first-class success, not an error. It is
what the system returns when the corpus does not support an answer, and its rate
is a headline evaluation metric rather than a failure count.

### Citation

Binds one claim in an answer to the evidence supporting it. This table is what
makes an answer checkable.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | |
| `workspace_id` | `uuid` | |
| `run_id` | `uuid` | |
| `claim_index` | `int` | Position of the claim within the answer. |
| `claim_text` | `text` | The claim as asserted. |
| `document_id` | `uuid` | Where the support came from. |
| `chunk_id` | `uuid` | The specific retrieved unit. |
| `char_start` / `char_end` | `int` | Span within the document text. |
| `support` | `text` | `supported` \| `partial`. |
| `score` | `float` | Retrieval or validation score. |
| `created_at` | `timestamptz` | |

There is no `unsupported` value: a claim that reaches that verdict does not get
emitted, so it never produces a citation row. Storing the span, not just the
chunk id, means the UI can highlight the supporting sentence and that the
citation survives re-chunking.

## Graph model (Neo4j)

### Node labels

Every node carries `workspace_id`, and every node derived from text carries
provenance.

| Label | Represents | Key properties |
| --- | --- | --- |
| `:Company` | A company or organisation under research. | `entity_id`, `name`, `aliases`, `website`, `confidence` |
| `:Person` | An individual — founder, executive, operator. | `entity_id`, `name`, `aliases`, `role_titles`, `confidence` |
| `:Investor` | A party that deploys capital. | `entity_id`, `name`, `investor_type`, `confidence` |
| `:Product` | Something a company offers. | `entity_id`, `name`, `category`, `confidence` |
| `:Market` | A market, sector or segment. | `entity_id`, `name`, `taxonomy_path`, `confidence` |
| `:FundingRound` | One financing event. | `entity_id`, `stage`, `amount`, `currency`, `announced_on`, `confidence` |
| `:Document` | Projection of the Postgres `Document` row. | `document_id`, `title`, `checksum_sha256` |
| `:Chunk` | One retrievable unit of text, and its vector. | `chunk_id`, `document_id`, `ordinal`, `text`, `char_start`, `char_end`, `embedding`, `embedding_model` |

`:Investor` is a role, not a disjoint category. A venture firm is
`(:Investor)`; a corporate investor is `(:Company:Investor)` — the same node,
two labels — so that "which companies invested in X" and "which companies does X
compete with" reach the same entity rather than a duplicate of it. Entity
resolution assigns the labels; extraction only proposes them.

`:Document` and `:Chunk` are the bridge between text and meaning. They are the
reason a graph assertion can be traced back to a sentence.

### Relationships

| Relationship | Pattern | Meaning |
| --- | --- | --- |
| `FOUNDED` | `(:Person)-[:FOUNDED]->(:Company)` | Founder of. |
| `INVESTED_IN` | `(:Investor)-[:INVESTED_IN]->(:Company)` | Holds or held a position. |
| `OPERATES_IN` | `(:Company)-[:OPERATES_IN]->(:Market)` | Active in a market. |
| `OFFERS` | `(:Company)-[:OFFERS]->(:Product)` | Sells or ships. |
| `RAISED` | `(:Company)-[:RAISED]->(:FundingRound)` | Was the subject of a round. |
| `LED_BY` | `(:FundingRound)-[:LED_BY]->(:Investor)` | Lead investor on a round. |
| `COMPETES_WITH` | `(:Company)-[:COMPETES_WITH]->(:Company)` | Competitive overlap. |
| `HAS_CHUNK` | `(:Document)-[:HAS_CHUNK]->(:Chunk)` | Structural containment. |
| `MENTIONS` | `(:Chunk)-[:MENTIONS]->(entity)` | This text refers to this entity. |

`COMPETES_WITH` is semantically symmetric but stored once, in a canonical
direction (ordered by `entity_id`), and traversed without direction in Cypher.
Writing both directions doubles the write path and invites the two copies to
disagree.

`RAISED` plus `LED_BY` model a round as a node rather than an edge, because a
round has its own attributes (amount, date, stage) and several participants.
`INVESTED_IN` is the denormalised shortcut for the common one-hop question; it
is derived from round participation, not asserted independently of it.

### Properties carried by every derived element

Nodes and relationships extracted from text carry:

| Property | Purpose |
| --- | --- |
| `workspace_id` | Isolation. Filtered by every query template. |
| `source_document_ids` | Which documents support this. |
| `source_chunk_ids` | Which chunks support this — the citation anchor. |
| `confidence` | Extractor confidence, `0.0`–`1.0`. |
| `extraction_job_id` | The `IngestionJob` that wrote it. |
| `pipeline_version` | Which code produced it. |
| `created_at` / `updated_at` | |

An assertion seen in several chunks accumulates `source_chunk_ids` rather than
producing a duplicate relationship. Corroboration is a property of an edge, not
a reason to have more edges.

### Indexes and constraints

- A **vector index** on `:Chunk(embedding)` — cosine similarity, dimension from
  `EMBEDDING_DIMENSIONS` (1536 for `text-embedding-3-small`). Changing the model
  means re-embedding and rebuilding this index; see
  [ADR 001](adr/001-neo4j-for-vector-and-graph-retrieval.md).
- A **full-text index** on entity `name` and `aliases`, to anchor a question's
  named entities onto graph nodes before traversal.
- **Uniqueness constraints** on a single `uid` property per label, where
  `uid = "<workspace_id>:<entity_id>"`. A single-property constraint is used
  deliberately: node key constraints are a Neo4j Enterprise feature, and the
  development stack runs `neo4j:5.26-community`. Composing the workspace into
  the key makes cross-workspace collision impossible by construction.
- **Range indexes** on `workspace_id` for each label, since it is a predicate on
  essentially every query.

### Workspace scoping in practice

Neo4j has no row-level security, so isolation is application-enforced and must
be visible at every call site. Two rules:

1. Every query template takes `workspace_id` as a bound parameter and filters on
   it in the `MATCH`, never in post-processing.
2. Vector search is the exception that needs care. A nearest-neighbour query
   returns the global top *k*, so it is issued with an over-fetch and then
   filtered to the workspace, and the effective *k* after filtering is asserted
   rather than assumed. Alternatives — a per-workspace index, or a separate
   database per workspace — are the escape hatch if over-fetch proves costly.
   This is called out as a known sharp edge in
   [ADR 001](adr/001-neo4j-for-vector-and-graph-retrieval.md).

## Worked example: how a claim becomes checkable

An answer says *"Acme raised a $12M Series A led by Initech Ventures."*

```
Citation row       claim -> chunk_id, document_id, char span
   |
   v
(:Chunk {chunk_id})  --[:MENTIONS]--> (:FundingRound {stage:"Series A"})
   ^                                        |
   | HAS_CHUNK                              | LED_BY
   |                                        v
(:Document {document_id})            (:Investor {name:"Initech Ventures"})
   |
   v
Postgres Document row -> storage_path -> the uploaded PDF
```

Every arrow is stored, not inferred at read time. That is what makes the chain
from rendered sentence back to source PDF a lookup rather than a reconstruction.
