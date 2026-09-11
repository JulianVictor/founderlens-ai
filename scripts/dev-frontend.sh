#!/usr/bin/env bash
# Start the Next.js dev server on http://localhost:3000
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec npm --prefix "$repo_root/frontend" run dev
