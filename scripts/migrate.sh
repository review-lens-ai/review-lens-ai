#!/usr/bin/env bash
# Applies supabase/migrations/*.sql to the database in backend/.env (DATABASE_URL).
# Usage: ./scripts/migrate.sh [--status]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$ROOT_DIR/backend/.env" ]; then
  echo "Missing backend/.env — copy backend/.env.example and fill in values." >&2
  exit 1
fi

cd "$ROOT_DIR/backend"
uv run python -m app.db.migrate "$@"
