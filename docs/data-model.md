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

**Soft delete at the top** *(planned, not in the MVP)*. Deleting a workspace
will set `deleted_at` and schedule purge of its Storage objects and its graph
partition. Graph data is rebuildable, so purging it is not destructive of
anything authoritative. Phase 1 deletes a workspace outright, cascading to its
memberships.

## Relational model (Supabase PostgreSQL)

Identity comes from Supabase Auth; `user_id` always references `auth.users(id)`.

### Profile

Application-owned data about an authenticated user. `auth.users` belongs to
Supabase Auth and is not extended directly, so anything the application knows
about a person lives here. Created by the signup trigger, in the same
transaction as the account.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | References `auth.users(id)`, cascade on delete. |
| `display_name` | `text` | Non-blank, 1-100 characters. |
| `created_at` | `timestamptz` | |

**Implemented** in `20260911000100_create_profiles.sql`.

### Workspace

The isolation boundary. Everything else hangs off it.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | `gen_random_uuid()`. |
| `name` | `text` | Non-blank, 1-100 characters. |
| `created_by` | `uuid` | `auth.users(id)`. Historical record, *not* an authorization input. |
| `created_at` | `timestamptz` | |

`created_by` is deliberately not consulted when deciding access. Membership in
`workspace_members` is the only authority, so transferring ownership never
requires rewriting history. A `slug`, `updated_at` and soft deletion are
expected later; they are not in the MVP because nothing yet needs them.

There is **no INSERT policy** on this table. A directly-inserted workspace would
have no members and so be invisible to its own creator. Creation goes through
`public.create_workspace(name)`, which inserts the workspace and its owner
membership in one transaction, or through the signup trigger.

**Implemented** in `20260911000200_create_workspaces.sql`.

### WorkspaceMember

Who may see a workspace, and at what level. This table is the sole authority
for access; no other check may substitute for it.

| Column | Type | Notes |
| --- | --- | --- |
| `workspace_id` | `uuid` PK part | Cascade on workspace delete. |
| `user_id` | `uuid` PK part | `auth.users(id)`, cascade on delete. |
| `role` | `public.workspace_role` | `owner` \| `member`. |
| `created_at` | `timestamptz` | |

Primary key `(workspace_id, user_id)`, with a secondary index on `user_id` for
the "which workspaces does this user belong to" direction.

The MVP has two roles. `admin` and `viewer` are plausible later additions, but
each new role has to be placed in the privilege ordering deliberately -- see
`WorkspaceRole.rank` in `app/models/workspace.py`, which keeps the ordering as
an explicit table rather than an accident of declaration order.

A trigger prevents removing or demoting the last `owner`: an ownerless
workspace can never have its membership changed again.

**Implemented** in `20260911000300_create_workspace_members.sql`, with policies
in `20260911000400_workspace_access_policies.sql`.

### Row-level security and the two access paths

The browser talks to Supabase directly with the user's own token, and RLS is
what isolates it. The FastAPI backend connects as a privileged role that RLS
does not constrain, so on that path isolation is enforced by the repository and
service layers: every query is scoped by `user_id`, and workspace access goes
through `WorkspaceService.require_membership`. Both mechanisms are load-bearing
and neither is a fallback for the other.

Policies consult two `SECURITY DEFINER` helpers, `is_workspace_member()` and
`is_workspace_owner()`. They must be `SECURITY DEFINER`: the natural policy on
`workspace_members` has to query `workspace_members` to decide, which
re-triggers the same policy and is rejected as infinite recursion. Both are
`STABLE`, read-only, pinned to an explicit `search_path`, and test `auth.uid()`
rather than accepting a user id, so a caller cannot ask about somebody else.

Every protected table also has `FORCE ROW LEVEL SECURITY`, which applies the
policies to the table owner too. Without it the owning role bypasses every
policy.

### Document

Metadata for one uploaded file. The bytes live in Supabase Storage; this row
points at them and is authoritative for everything about them.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | Reused as the `:Document` node id in Neo4j. |
| `workspace_id` | `uuid` | Cascade on workspace delete. |
| `uploaded_by` | `uuid` | `auth.users(id)`. Historical record, not an authorization input. |
| `filename` | `text` | The name the user's file had. **Display only.** |
| `storage_path` | `text` | Object key. Unique globally. |
| `mime_type` | `text` | The type the server **detected from the bytes**. |
| `size_bytes` | `bigint` | Positive. |
| `checksum_sha256` | `text` | Dedup key and provenance anchor; `^[0-9a-f]{64}$`. |
| `status` | `public.document_status` | `uploaded` \| `processing` \| `ready` \| `failed`. |
| `error_message` | `text` NULL | Only when `failed`, enforced by a check constraint. |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` maintained by trigger, never client-supplied. |

Unique on `(workspace_id, checksum_sha256)`: re-uploading the same bytes into a
workspace resolves to the existing document instead of duplicating the corpus,
which would let one source count twice in retrieval. Dedup is deliberately *per
workspace* -- two teams researching the same company must both be able to hold
the same filing.

**`filename` is never used to build a path and never trusted to say what the
file contains.** It is sanitised to a single path segment of printable
characters before being stored, because it is rendered in a UI and written to
logs. `mime_type` comes from inspecting the bytes -- see
[`ai-pipeline.md`](ai-pipeline.md) for why an extension is not evidence.

The id is minted by the application rather than by the column default. It has
to be: the storage key is derived from it, and the bytes are written before the
row exists, so it must be known in advance. It is still a server-generated
UUIDv4 -- the identity rule that matters is that no LLM proposes it.

**Implemented** in `20260911000600_create_documents.sql`.

### Document storage

Object keys are `<workspace_id>/<document_id>.<ext>`, where the extension comes
from the *detected* type. Every segment is a UUID or a server-chosen constant,
so path traversal and cross-workspace key collisions are impossible by
construction rather than by validation.

The `research-documents` bucket is private and **no policy grants
`authenticated` any access to it**. A browser holding a user's session cannot
read, write or list an object there. This is deliberate: every upload has to be
inspected server-side before it is accepted, and a direct-to-storage upload
would bypass that. All access is mediated by the API using the service-role key,
after a membership check.

Postgres cannot reach into Storage, so deleting a document row does not delete
its object. Removal is ordered by the API -- derived data, then the object, then
the row -- so that a failure leaves a retryable document rather than orphaned
bytes. See `DocumentService.delete`.

**Implemented** in `20260911000700_create_documents_bucket.sql`.

### IngestionJob

One run of the pipeline over one document. Exists so that a failure is
diagnosable and retryable rather than an opaque status flag.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | |
| `workspace_id` | `uuid` | Cascade on workspace delete. |
| `document_id` | `uuid` | Cascade on document delete. |
| `status` | `public.ingestion_job_status` | `queued` \| `running` \| `succeeded` \| `failed` \| `cancelled`. |
| `current_stage` | `public.ingestion_stage` | Furthest stage reached: `upload` \| `extract` \| `clean` \| `chunk` \| `persist`. |
| `attempt` | `int` | Which attempt this is for the document. |
| `error_message` | `text` NULL | Only when failed, enforced by a check constraint. |
| `chunk_count` | `int` NULL | Set on success. |
| `pipeline_version` | `text` | Which code produced the result. |
| `started_at` / `completed_at` | `timestamptz` NULL | |
| `created_at` / `updated_at` | `timestamptz` | `updated_at` maintained by trigger. |

The stage is recorded *before* each step runs, so a job killed mid-flight
leaves behind the stage it was in rather than the last one it finished.
`embed` and `graph` are deliberately absent from the enum: a stage the pipeline
cannot reach would make `current_stage` a claim rather than a fact.

A **retry is a new row**, never a mutated one, so the history of what was tried
survives. `completed_at` is set exactly when the status becomes terminal, so
"is it finished" has one answer rather than two that can disagree.

**The idempotency guard** is a partial unique index: at most one job per
document may be `queued` or `running`. It lives in the database because two
concurrent process requests can both read "no active job" before either writes
one — only the database can settle that race. A second request therefore joins
the run already under way instead of starting a rival one.

**Implemented** in `20260911000800_create_ingestion_jobs.sql`.

### DocumentChunk

The output of deterministic ingestion, held relationally until the graph phase
indexes it into Neo4j. Postgres is the staging ground, not the destination:
keeping chunks here means embedding and graph construction can be re-run from
stored chunks instead of re-parsing every document.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `uuid` PK | The deterministic chunk id, supplied by the pipeline rather than defaulted. |
| `workspace_id` / `document_id` | `uuid` | Both cascade on delete. |
| `chunk_index` | `int` | Position in the document, 0-based. |
| `text` | `text` | Non-blank. |
| `token_count` | `int` | |
| `page_number` | `int` NULL | Page the chunk starts on. |
| `page_numbers` | `int[]` | Every page it covers. |
| `start_offset` / `end_offset` | `int` | Span in the cleaned text; slicing by these reproduces `text`. |
| `source_start_offset` / `source_end_offset` | `int` | Span in the parser's output — the provenance anchor. |
| `content_hash` | `text` | SHA-256 of `text`. |
| `tokenizer` | `text` | Counts from different tokenizers are not comparable. |
| `ingestion_job_id` | `uuid` NULL | The run that last wrote the row; nulled rather than cascading, so pruning the audit trail does not delete evidence. |
| `created_at` | `timestamptz` | |

Unique on `(document_id, chunk_index)`. Together with the deterministic id,
that is what makes reprocessing a **replace** rather than an append: the
pipeline deletes the chunks the new run did not produce and upserts the rest,
in one transaction. Re-running over unchanged bytes recomputes the same ids and
updates the same rows, so `created_at` still says when each chunk first
appeared — visible evidence that nothing was recreated.

**Implemented** in `20260911000900_create_document_chunks.sql`.

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
- **Uniqueness constraints** on a single property per label. Node key
  constraints are a Neo4j Enterprise feature and the development stack runs
  `neo4j:5.26-community`, so a single-property constraint is used throughout.
  `:Document` and `:Chunk` constrain `id` directly, because those ids are
  UUIDs minted by Postgres and already unique across every workspace. Entities
  the resolution pipeline mints get a composed
  `uid = "<workspace_id>:<entity_id>"`, where composing the workspace into the
  key is what makes cross-workspace collision impossible.
- **A mandatory-property constraint** on `workspace_id` for `:Document` and
  `:Chunk`. Neo4j has no row-level security, so a node without it would be
  invisible to every scoped query and returned to everyone by one that forgot
  to scope.
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
