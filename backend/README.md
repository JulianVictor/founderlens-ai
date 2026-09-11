# FounderLens AI — Backend

FastAPI service for the FounderLens research pipeline. Python 3.12, Pydantic v2.

```bash
pip install -e "backend[dev]"          # from the repository root
uvicorn app.main:app --reload --app-dir backend
```

`GET /health` → `{"status": "ok", "service": "founderlens-api"}`

Checks:

```bash
pytest backend/tests
ruff check backend && ruff format --check backend
(cd backend && mypy)
```

Configuration is read from a `.env` at the repository root (see `../.env.example`)
into the typed `app.core.config.Settings` model. Secrets are held as `SecretStr`
and never appear in a repr or log line.

Layering — `api/` → `services/` → `repositories/`, with `ai/` holding the
research pipeline. See [`../docs/architecture.md`](../docs/architecture.md).
