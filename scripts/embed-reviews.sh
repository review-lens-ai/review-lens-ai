#!/usr/bin/env bash
# Embeds every review in backend/.env's database (DATABASE_URL) that has no
# embedding yet. Safe to run again: reviews that already have one are skipped.
# Run ./scripts/migrate.sh first. Usage: ./scripts/embed-reviews.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$ROOT_DIR/backend/.env" ]; then
  echo "Missing backend/.env — copy backend/.env.example and fill in values." >&2
  exit 1
fi

cd "$ROOT_DIR/backend"
uv run python -m app.services.embed_reviews
