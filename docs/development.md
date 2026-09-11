# Local development

## Prerequisites

- Python 3.12
- Node.js 20+
- Docker (for Neo4j)
- Supabase CLI (for the local Supabase stack)

## Backend

```bash
python -m venv .venv
source .venv/Scripts/activate     # Windows; use .venv/bin/activate elsewhere
pip install -e "backend[dev]"

cp .env.example .env              # fill in real values
uvicorn app.main:app --reload --app-dir backend
```

Checks:

```bash
pytest backend/tests
ruff check backend
ruff format --check backend
(cd backend && mypy)
```

## Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
npm run dev
npm run lint && npm run typecheck && npm run build
```

## Infrastructure

```bash
cp infra/docker/.env.example infra/docker/.env
docker compose -f infra/docker/docker-compose.yml up -d
```

## Convenience scripts

`scripts/dev-backend.sh`, `scripts/dev-frontend.sh` and `scripts/check.sh` wrap
the commands above.

## Secrets

`.env`, `.env.local` and every real key stay out of git. Only `*.env.example`
files are committed, and they contain placeholders.
