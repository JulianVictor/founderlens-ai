#!/usr/bin/env bash
# Start the API with autoreload on http://localhost:8000
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec uvicorn app.main:app --reload --app-dir "$repo_root/backend" --port "${PORT:-8000}"
