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

Text extraction, cleaning and chunking live in `ai/ingestion/`. Entity
extraction lives in `ai/extraction/`. The two "extraction" names refer to
different operations; see the note in
[`architecture.md`](architecture.md#ingestion-pipeline).

### `ingestion/` — text extraction

**Responsibility.** Turn uploaded bytes into text, preserving enough structure
to locate any character later.

Handles the format-specific work (PDF, DOCX, HTML, plain text) and emits text
plus an offset map — page and section boundaries expressed as character ranges.
That offset map is the foundation of every citation: a claim is ultimately a
character span in a document, and a span is only meaningful if the text it
indexes is stable. Extraction output is therefore persisted, not recomputed.

**Does not:** interpret meaning, summarise, or call an LLM.

### `ingestion/` — cleaning

**Responsibility.** Normalise text without destroying offsets.

Whitespace and encoding normalisation, de-hyphenation across line breaks,
removal of repeated headers, footers and page furniture. Every edit is recorded
as a transformation against the original offsets, so a span in cleaned text
still resolves to a span in the source file. Cleaning that silently reindexes
text breaks citations in a way that is very hard to notice.

**Does not:** paraphrase or rewrite. Cleaning is lossless with respect to
meaning.

### `ingestion/` — chunking

**Responsibility.** Split cleaned text into units that are good to retrieve and
good to cite.

Chunks respect structure (section and paragraph boundaries) before length, carry
a bounded overlap so a fact spanning a boundary is not lost, and record
`document_id`, `ordinal` and `char_start`/`char_end`. The chunk is the unit of
retrieval, the unit of embedding and the unit of citation simultaneously, which
is why its boundaries matter more than any other single ingestion parameter.

Per the engineering rules, a whole document is never sent to the LLM where
chunking is appropriate.

### `embeddings/`

**Responsibility.** Turn chunk text into vectors, behind a provider interface.

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

### `graph/`

**Responsibility.** All Neo4j access — writes, and the constrained read
templates.

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
