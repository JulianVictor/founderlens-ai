# FounderLens AI

Evidence-grounded startup research. Upload a company's documents into a
workspace; FounderLens chunks and embeds them, extracts entities and
relationships into a knowledge graph, and answers research questions with
citations back to the source text — or an explicit *insufficient evidence*
result when the corpus does not support an answer.

The point of the system is that every claim is checkable: retrieval traces,
supporting chunks, graph paths and evaluation results are all inspectable.

> **Status: Phase 0.** This repository currently contains the foundations —
> project structure, typed configuration, quality gates, a `GET /health`
> endpoint and a minimal frontend shell. The research pipeline itself is not
> implemented yet.

## Architecture

| Layer | Choice |
| --- | --- |
| Frontend | Next.js (App Router), TypeScript strict, Tailwind |
| Backend | Python 3.12, FastAPI, Pydantic v2 |
| App data | Supabase — Postgres, Auth, Storage |
| Graph | Neo4j |
| Providers | LLM and embedding providers selected by configuration |
| Quality | pytest, Ruff, mypy (strict), ESLint, `tsc --noEmit` |

The backend is layered — `api/` → `services/` → `repositories/` — with the
research pipeline isolated under `app/ai/` (ingestion, embeddings, extraction,
entity resolution, graph, retrieval, generation, citations, evaluation). Route
handlers stay thin; decisions live in services and the pipeline.

Invariants that hold across every phase: workspace-scoped isolation on every
query, provenance on every chunk and graph element, no claim without retrieved
evidence, entity identity assigned by us rather than by the LLM, and no
free-form LLM-authored Cypher. See [`docs/architecture.md`](docs/architecture.md).

## Repository structure

```
frontend/   Next.js application
backend/    FastAPI service
  app/
    api/           HTTP routes
    core/          typed settings, logging
    models/        persistence shapes
    schemas/       API contracts
    repositories/  I/O against Postgres, Storage, Neo4j
    services/      use-case orchestration
    ai/            the research pipeline
  tests/
supabase/   database migrations and local stack notes
infra/      Docker Compose for local Neo4j
evals/      retrieval and grounding evaluation datasets
docs/       architecture and development documentation
scripts/    dev and check helpers
```

## Local development

Prerequisites: Python 3.12, Node.js 20+, Docker, and the Supabase CLI.

```bash
git clone <repo> && cd founderlens-ai
cp .env.example .env                 # placeholders only — fill in real values
```

Backend — <http://localhost:8000>:

```bash
python -m venv .venv && source .venv/Scripts/activate   # .venv/bin/activate elsewhere
pip install -e "backend[dev]"
uvicorn app.main:app --reload --app-dir backend
```

```bash
curl http://localhost:8000/health
# {"status":"ok","service":"founderlens-api"}
```

Frontend — <http://localhost:3000>:

```bash
cd frontend && npm install
cp .env.example .env.local
npm run dev
```

Neo4j:

```bash
cp infra/docker/.env.example infra/docker/.env      # set NEO4J_PASSWORD
docker compose -f infra/docker/docker-compose.yml up -d
```

Full details in [`docs/development.md`](docs/development.md).

## Checks

```bash
./scripts/check.sh
```

which runs `ruff check`, `ruff format --check`, `mypy`, `pytest`, and the
frontend `lint` and `typecheck`.

## Configuration and secrets

All backend configuration is validated once into the typed
`app.core.config.Settings` model; secrets are `SecretStr` and never appear in a
repr or a log line. Only `NEXT_PUBLIC_*` values are readable by the browser —
the Supabase service-role key and provider API keys are backend-only.

Committed `*.env.example` files contain placeholders. Real `.env` files, keys
and passwords are never committed.
