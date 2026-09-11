# Architecture

## Layering

The backend is layered, and dependencies point in one direction only:

```
api/           HTTP transport. Thin handlers, no domain logic.
  ↓
services/      Orchestration: use cases, transaction boundaries.
  ↓
repositories/  All I/O against Postgres, Storage and Neo4j.
models/        Persistence shapes (rows, nodes, relationships).
schemas/       Pydantic contracts crossing the API boundary.
core/          Config, logging, shared primitives.
ai/            The research pipeline (see below).
```

A route handler resolves its dependencies, calls one service and serialises the
result. Anything that looks like a decision belongs in `services/` or `ai/`.

## AI pipeline

```
ingestion  →  embeddings  ─┐
    │                      ├→  retrieval  →  generation  →  citations
    └→ extraction → entity_resolution → graph ─┘
                                                    evaluation (observes all of it)
```

| Package | Responsibility |
| --- | --- |
| `ingestion` | Load documents, normalise, chunk with provenance. |
| `embeddings` | Provider interface + chunk vectorisation. |
| `extraction` | Structured entity/relationship extraction via validated outputs. |
| `entity_resolution` | Map extracted mentions to canonical entity IDs. |
| `graph` | Workspace-scoped graph writes and template-constrained reads. |
| `retrieval` | Vector, graph and hybrid retrieval producing ranked evidence. |
| `generation` | Grounded answers over retrieved evidence only. |
| `citations` | Bind each claim to the evidence supporting it. |
| `evaluation` | Retrieval and grounding metrics. |

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

## Provider abstraction

LLM and embedding providers sit behind interfaces from the start, because both
are genuinely swappable and selected by configuration. Nothing else is
abstracted until a second real implementation exists.
