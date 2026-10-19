# AI pipeline

Component-by-component reference for the FounderLens research pipeline. For the
stores these components write to, see [`data-model.md`](data-model.md); for how
the pipeline sits in the system, see [`architecture.md`](architecture.md).

Every component below maps to a package under `backend/app/ai/`.

## FounderLens is not agent-based

This is a design decision, stated up front because it constrains everything
else.

The MVP pipeline is a **fixed, ordered sequence of steps**. It is not an agent
loop. Concretely, for the MVP:

- **No autonomous tool selection.** The pipeline does not hand an LLM a set of
  tools and let it decide which to call. Which retrievers run is decided by the
  retrieval planner, whose output is a validated structure from a closed set of
  options — not an open-ended action.
- **No self-directed iteration.** There is no "keep going until you are
  satisfied" loop. Every stage runs a bounded number of times. Retries are
  mechanical (transient provider failure), not strategic.
- **No LLM-authored control flow.** The LLM never decides what the program does
  next. It is called at specific points, with a specific prompt, to produce a
  specific validated structure.
- **No LLM-authored Cypher.** Graph access goes through parameterised templates.
  The model may select a template and supply parameters; it may not write the
  query.
- **No LangGraph, and no agent framework**, unless and until a requirement
  appears that genuinely needs one.

The reasons are practical rather than ideological:

**Evaluability.** A fixed pipeline has fixed stage boundaries, so recall can be
measured at retrieval, precision at citation validation, and a regression
localised to a stage. An agent that reaches an answer by a different route each
run makes "did retrieval get worse?" an unanswerable question.

**Cost and latency are bounded.** The number of LLM calls per question is known
in advance from the plan. An agent loop's cost is a distribution with a tail.

**Failures are diagnosable.** When a fixed pipeline produces a bad answer, the
trace says which stage produced the bad intermediate. When an agent produces a
bad answer, the trace says it made eleven calls.

**The hard problems here are not agentic.** Chunking quality, entity resolution,
evidence ranking and citation validation are the difficulty in this system, and
none is made easier by an agent. Adding one adds nondeterminism on top of
problems that are not yet solved.

What is deliberately left open: multi-hop retrieval — asking a follow-up
traversal based on what vector retrieval anchored on — is *planned* in the
planner and executed as a fixed two-phase step. Iterative retrieval is a
candidate for a later phase, and it would be introduced as a bounded loop with
an explicit iteration cap, not as an agent.

## Ingestion components

```
document -> extraction -> cleaning -> chunking -> embeddings
         -> entity extraction -> entity resolution -> graph persistence
```

### Orchestration  *(implemented)*

The deterministic half runs as an explicit pipeline with its stages named in
the database:

```
UPLOAD -> EXTRACT -> CLEAN -> CHUNK -> PERSIST -> EMBED -> INDEX
```

`UPLOAD` is where the pipeline *acquires* the stored bytes; the upload itself
happened earlier, when the document was accepted. `PERSIST` stages chunks in
Postgres, `EMBED` vectorises them and `INDEX` writes nodes and vectors into
Neo4j, so **`ready` now means a document is retrievable**. Entity extraction
and graph construction are still to come, and nothing answers questions yet.

`IngestionService` owns the sequence. Each stage is recorded before it runs, so
a job killed mid-flight leaves behind the stage it was in rather than the last
one it finished, and a failure says *where* it happened.

**Two kinds of idempotency, handled in different places.** Two requests to
process the same document are settled by the database: a partial unique index
allows one queued-or-running job per document, so a second request joins the
run already under way. Reading "is one active?" and then inserting would not
be enough, because two callers can both read "no". Processing the same document
twice *over time* is settled by deterministic chunk ids plus a replace: the
same bytes recompute the same ids and update the same rows, so nothing is
duplicated and nothing is orphaned.

**A retry is a new job**, not a mutated one, so the history of what was tried
survives. Running the same job twice is a no-op, because only a queued job can
start -- the half an at-least-once queue would otherwise expose.

Work runs in-process on the event loop behind a `TaskRunner` protocol. That is
a deliberate MVP choice with limits written down where the runner is defined:
work does not survive a restart, there is no cross-process coordination, and
there is no backpressure. Each is a reason to move to a real queue later, and
none was a reason to build one first. Replacing it is implementing the protocol
again, not rewriting the service.

Text extraction, cleaning and chunking live in `ai/ingestion/`. Entity
extraction lives in `ai/extraction/`. The two "extraction" names refer to
different operations; see the note in
[`architecture.md`](architecture.md#ingestion-pipeline).

### Before ingestion: accepting a document

Upload is not a pipeline stage, but it is what guarantees the pipeline's input.
A document is accepted only after the server has decided what it *is* by
reading it -- `app/core/content_detection.py`. The filename extension and the
multipart `Content-Type` are both client-controlled strings and neither is
believed.

This matters to the pipeline specifically: text extraction dispatches on the
recorded `mime_type`, so a file recorded as `application/pdf` because it was
*called* `report.pdf` would hand arbitrary bytes to a PDF parser. PDF has a
magic number and is checked for it; plain text and Markdown do not differ in
their bytes, so the real check is "does this decode as UTF-8 without NULs", and
the extension only chooses between the two text labels once that has passed.

MVP accepts PDF, plain text and Markdown, up to 25 MiB.

### `ingestion/` — text extraction  *(implemented)*

**Responsibility.** Turn uploaded bytes into text, preserving enough structure
to locate any character later.

A parser per format, selected by the recorded `mime_type` and never by a
filename, behind the `DocumentParser` protocol:

| Parser | Format | Notes |
| --- | --- | --- |
| `PDFParser` | `application/pdf` | PyMuPDF. Emits one `ExtractedPage` per page with its span, so page numbers survive. |
| `TextParser` | `text/plain` | Strict UTF-8 decode. |
| `MarkdownParser` | `text/markdown` | Keeps the markup; lifts YAML front matter into metadata. |

`ExtractedDocument.text` is the canonical text that source offsets index. For
a paginated format it is the pages joined by a single declared separator and
nothing else, so every page's recorded span indexes it exactly.

Three decisions worth stating:

- **Markdown keeps its markup.** Headings and list markers are the structure
  the chunker splits on, and they read correctly in a displayed citation. A
  de-marked-up approximation would chunk worse and cite worse.
- **No layout reconstruction.** No column detection or table rebuilding: those
  are heuristics that can be wrong, and a wrong reconstruction is harder to
  notice downstream than awkward reading order.
- **Decoding is strict.** The upload check sampled only the first 8 KB, so a
  file can arrive here and still be undecodable. Replacing bad bytes with
  U+FFFD would silently corrupt text a citation later quotes, so the document
  fails instead.

A PDF with no text layer — a scan — is reported as such rather than as an
empty document, because the fix (OCR) is different from the fix for a bad file.

**Does not:** interpret meaning, summarise, or call an LLM.

### `ingestion/` — cleaning  *(implemented)*

**Responsibility.** Normalise text without destroying offsets.

`TextCleaner` normalises line endings, removes zero-width characters and soft
hyphens, rejoins words broken across a line, collapses repeated spacing inside
a line, strips trailing whitespace, reduces runs of blank lines to one, and
trims the document.

The constraint that shapes it: cleaning is restricted to **deletions and
equal-length substitutions**. Neither can shift text by an amount a map cannot
account for, so the `OffsetMap` it returns is exact rather than approximate,
and every cleaned offset resolves to a real source offset. An approximate map
would be worse than none — a citation highlighting the wrong sentence is harder
to notice than one that admits it does not know. Transformations that cannot be
expressed that way, such as Unicode NFC normalisation, are out of scope by
construction rather than by discipline.

Leading indentation is preserved, because Markdown nesting and code blocks
depend on it.

**Not done yet:** repeated headers and footers are left in place. Detecting
them means deciding that a line appearing on most pages is furniture rather
than content, and that judgement is wrong often enough — a recurring section
title, a company name that is also the subject — that deleting the text would
lose evidence. It needs cross-page frequency analysis and a recorded decision,
and belongs in a later phase.

**Does not:** paraphrase or rewrite. Cleaning is lossless with respect to
meaning.

### `ingestion/` — chunking  *(implemented)*

**Responsibility.** Split cleaned text into units that are good to retrieve and
good to cite.

Defaults: **800 tokens, 120 tokens of overlap**, both configurable.

**Why the text is split on structure rather than on tokens.** The obvious
implementation encodes the document, slices the token array into windows and
decodes each one. It is wrong for this system: a decoded token slice has no
relationship to the original character positions, and a chunk whose offsets are
unknown cannot be cited. So the text is divided at boundaries that exist in it
— paragraphs, then sentences, then words, with a character split only for a
pathological run such as a base64 blob — and the tokenizer decides only how
many pieces fit. Offsets survive exactly, and chunks additionally respect the
document's own structure.

Each chunk records `chunk_id`, `document_id`, `workspace_id`, `text`,
`chunk_index`, `page_number` and every page it covers, `token_count`, its span
in the cleaned text, its span in the source text, and a `content_hash`.

- **`cleaned.text[start_offset:end_offset] == text`** is an exact invariant, so
  a chunk is verifiable against the document it was cut from.
- **Chunk ids are deterministic**: `uuid5` over document, index and content
  hash. Re-ingesting an unchanged document produces the same ids, so graph
  provenance does not churn; changed text produces a new id, because it is a
  different chunk and should not inherit the claims that cited the old one.
- **`content_hash` detects duplicate content** — boilerplate repeated across a
  corpus — without comparing text.
- **Overlap is a budget, not a promise.** It repeats whole paragraphs or
  sentences, since a piece is the smallest unit whose offsets are known. Where
  every trailing piece is larger than the budget, chunks come out disjoint.

Token counts come from a `Tokenizer` behind an interface: `tiktoken` where its
encoding table is reachable, and a logged fall back to an offline approximation
where it is not — ingestion should degrade to slightly imprecise chunk sizes
rather than stop. Counts are not additive under byte-pair encoding, so packing
uses the sum of the pieces as its budget and the count recorded on a finished
chunk is a fresh count of the text actually emitted.

Per the engineering rules, a whole document is never sent to the LLM where
chunking is appropriate.

### `embeddings/`  *(implemented)*

**Responsibility.** Turn chunk text into vectors, behind a provider interface.

Provider and model come entirely from configuration — `EMBEDDING_PROVIDER`,
`EMBEDDING_MODEL`, `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL` — and nothing
carries a usable default, so an unconfigured deployment fails at startup rather
than embedding against somebody's account. Spoken over HTTP rather than through
a vendor SDK, so any OpenAI-compatible endpoint works by changing a URL.

Two guarantees every implementation owes its callers:

- **Order is preserved.** `embed_texts` returns one vector per input, in the
  same order. The response is re-ordered by the index the provider reports
  rather than by array position — the API documents that results may arrive
  out of order, and a silently mis-ordered batch attaches each chunk's vector
  to a *different* chunk. That is wrong retrieval with no error anywhere.
- **Dimensions are checked**, here rather than at the index. A vector index
  rejects a mismatched vector with an error naming the index, not the model or
  the gateway that caused it.

Batches are issued sequentially. Providers rate-limit per minute, and the
document that fires twenty parallel requests is the one most likely to be
throttled — turning a slow ingestion into a failed one.

Defines the provider protocol, handles batching, retries and rate limits, and
records which model produced each vector. Vectors from different models are not
comparable, so `embedding_model` is stored on the chunk and on the job; a model
change is a re-embed, and the recorded value is what makes that detectable
rather than silent.

The same interface serves ingestion (embed chunks) and query time (embed the
question), because they must use the same model to be meaningful.

### `extraction/` — entity extraction

**Responsibility.** Find entities and relationships in a chunk, as validated
structured output.

Given chunk text, produce candidate `Company`, `Person`, `Investor`, `Product`,
`Market` and `FundingRound` mentions and the relationships between them, each
with the evidence span that supports it and a confidence.

Two rules are absolute:

- **Structured outputs mapped into Pydantic models.** No free-text parsing. A
  response that does not validate is a failure, retried or dropped — never
  coerced.
- **Entity IDs proposed by the LLM are never trusted.** Extraction produces
  *mentions*, not identities. The model may say "Acme Corp appears here"; it may
  not say which canonical entity that is. That decision belongs to the next
  stage.

**Does not:** deduplicate across chunks, or decide that two mentions are the
same thing.

### `entity_resolution/`

**Responsibility.** Decide identity. Map mentions onto canonical workspace
entities and mint the IDs.

Normalises surface forms, blocks candidates (by name, alias and full-text
index), scores them, and either links a mention to an existing entity or creates
one with a new UUID. Resolution is deterministic given the same inputs and
candidate set; where a model assists in scoring, its output is an input to the
decision rule, not the decision.

This is the component that decides whether "Acme", "Acme Corp" and "ACME, Inc."
are one node or three, and it is scoped to a workspace: entities are never
merged across workspaces, because that would leak the existence of one
workspace's data into another.

### `graph/`  *(partly implemented)*

**Responsibility.** All Neo4j access — writes, and the constrained read
templates.

Implemented so far: the schema (constraints, property indexes, a cosine vector
index on `:Chunk(embedding)`, a full-text index on `:Chunk(text)`) and the
`:Document`/`:Chunk` upsert with `HAS_CHUNK`. Entity nodes and retrieval
templates are later.

Re-indexing is a replace, not an append: stale chunks are `DETACH DELETE`d
*before* the new ones are merged, because the other order leaves a window in
which the document has both its old and new chunks. `MERGE` on the
deterministic chunk id — and on the relationship — means a second run
updates the same nodes and adds no second edge.

**Every node carries `workspace_id`, and a constraint makes it mandatory.**
Neo4j has no row-level security, so that property plus a filter in every
template is the entire isolation guarantee on this path; a node without it
would be returned to anyone by a query that forgot to scope.

On the write side: upsert nodes and relationships idempotently keyed by
`(workspace_id, entity_id)`, attach provenance (`source_chunk_ids`,
`confidence`, `extraction_job_id`, `pipeline_version`), accumulate corroboration
onto existing edges rather than duplicating them, and maintain the vector and
full-text indexes.

On the read side: own the library of parameterised Cypher templates. Callers
select a template and supply parameters; nobody, human or model, passes a query
string through. Every template filters on `workspace_id`, and read paths use
read-only transactions so that a mistake cannot mutate the graph.

### Graph persistence and idempotency

Re-running ingestion for a document must converge, not accumulate. Writes are
keyed so that a second run of the same pipeline version over the same document
produces the same graph; a run at a *newer* pipeline version supersedes the
previous run's contributions for that document. This is what makes "rebuild the
workspace's graph" a routine operation rather than a risk.

## Query components

```
question -> query analysis -> retrieval planning -> vector retrieval
         -> graph retrieval -> evidence merge -> answer generation
         -> citation validation
```

### `retrieval/` — query analysis

**Responsibility.** Understand what is being asked, as structure.

Classifies intent (factual lookup, comparison, relationship, timeline),
identifies named entities and resolves them against the workspace's graph, and
extracts constraints such as time bounds. Output is a validated model, not prose.

Anchoring entities here is what makes graph retrieval possible at all: a
traversal needs a starting node, and that node must be a real entity in *this*
workspace.

### `retrieval/` — retrieval planning

**Responsibility.** Decide which retrievers to run, over what, and how much.

Chooses vector, graph, or both; sets *k*, traversal depth and the context
budget; decides whether a comparison question needs a sub-retrieval per subject.
The plan is a validated structure drawn from a closed set of options, and it is
persisted on the `ResearchRun`, which makes retrieval behaviour reviewable after
the fact.

This is the component most likely to be mistaken for an agent. It is not: it
produces a plan from a fixed option space in one step, and the executor follows
it. It does not act, observe and re-plan.

### `retrieval/` — vector retrieval

**Responsibility.** Find semantically similar chunks within the workspace.

Embeds the question with the same model used at ingestion, queries the
`:Chunk(embedding)` vector index, over-fetches and filters to `workspace_id`,
and asserts that enough results survive the filter. Returns chunks with scores
and full provenance attached — a result without provenance cannot be cited and
is therefore useless downstream.

### `retrieval/` — graph retrieval

**Responsibility.** Find evidence reachable by relationship rather than by
similarity.

From the anchor entities, traverse bounded paths using the constrained templates
in `graph/` — *investors in companies competing with X*, *rounds this person's
companies raised* — and collect the chunks that support each traversed edge.

This is where multi-hop questions are actually answered. Vector search finds text
that resembles the question; graph traversal finds facts that are several
relationships away from anything the question literally says. Both return
chunks, so both are citable in the same way.

### `retrieval/` — evidence merge

**Responsibility.** Turn two result sets into one ranked, deduplicated,
budget-bounded evidence list.

Deduplicates chunks arriving from both retrievers, reconciles their
incomparable scores into a single ranking, keeps the graph path that justified
each graph-sourced chunk, diversifies across documents so one verbose source
cannot crowd out the corpus, and trims to the context budget.

Trimming is the point at which grounding can silently fail — evidence dropped
here is evidence the generator cannot use — so what was dropped is recorded in
the trace.

### `generation/`

**Responsibility.** Compose an answer from the merged evidence, and only from it.

The generator receives evidence with stable references and is instructed to
answer using nothing else. It emits an answer whose claims are individually
attributable, and it has a first-class way to decline: when the evidence does
not support an answer, it returns `insufficient_evidence`.

That outcome is a success, not an error. A system that always answers is a
system whose answers carry no information about whether the corpus contained
one. Prompting alone is not treated as sufficient to guarantee grounding, which
is why the next stage exists.

### `citations/`

**Responsibility.** Verify, per claim, that the evidence actually supports it —
and enforce the consequence.

Segments the answer into claims, binds each to the evidence it cites, and checks
that the cited span supports the claim rather than merely being topically
related. Verdicts are `supported`, `partial` or `unsupported`.

Enforcement is the part that matters:

- `supported` and `partial` claims are emitted and written as `Citation` rows
  with their document, chunk and character span.
- `unsupported` claims are **not emitted**. There is no `unsupported` value in
  the `Citation` table because such a claim never reaches it.
- If what survives no longer answers the question, the whole run returns
  `insufficient_evidence`.

This is the gate that makes the grounding invariant real. Without it, grounding
is a request to the model rather than a property of the system.

### `evaluation/`

**Responsibility.** Measure whether any of the above is working.

Runs question sets from [`evals/datasets/`](../evals/README.md) through the
pipeline and reports:

| Metric | Question it answers |
| --- | --- |
| Retrieval recall@k | Did retrieval surface the chunks that contain the answer? |
| MRR | How highly were they ranked? |
| Citation precision | Of emitted claims, how many are genuinely supported? |
| Abstention rate | How often does the system say it does not know? |
| Correct abstention | Of those, how many *should* have been abstentions? |

Abstention is tracked from both directions on purpose. A system that never
abstains is ungrounded; a system that always abstains is useless. The pair of
metrics is what distinguishes the two, and neither alone would.

Harness code lives here rather than in `evals/` so that it shares the
pipeline's own types; `evals/` holds the datasets and reports.

## Cross-cutting rules

**Provenance flows forward, never regenerated.** Every stage attaches
provenance and passes it on. No stage reconstructs where something came from.

**Validated structure at every LLM boundary.** Every model call returns a
Pydantic-validated structure. A response that does not validate is an error.

**Workspace scope is a parameter, never an assumption.** Every retrieval,
traversal and write takes `workspace_id` explicitly.

**Everything is traceable.** Plan, retrieved chunks, scores, paths, model
versions and timings are persisted on the `ResearchRun`. An answer that cannot
be explained after the fact is not finished.
