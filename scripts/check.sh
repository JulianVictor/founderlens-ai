#!/usr/bin/env bash
# Run every quality gate the Definition of Done requires.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

echo "==> ruff check"
ruff check backend
echo "==> ruff format --check"
ruff format --check backend
echo "==> mypy"
(cd backend && mypy)
echo "==> pytest"
pytest backend/tests

echo "==> frontend lint"
npm --prefix frontend run lint
echo "==> frontend typecheck"
npm --prefix frontend run typecheck

echo "All checks passed."
