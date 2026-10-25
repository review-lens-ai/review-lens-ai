#!/usr/bin/env bash
# Runs the frontend (Next.js) and backend (FastAPI) dev servers together.
# Ctrl+C stops both.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$ROOT_DIR/backend/.env" ]; then
  echo "Missing backend/.env — copy backend/.env.example and fill in values." >&2
  exit 1
fi

if [ ! -f "$ROOT_DIR/frontend/.env.local" ]; then
  echo "Missing frontend/.env.local — copy frontend/.env.example and fill in values." >&2
  exit 1
fi

cleanup() {
  trap - SIGINT SIGTERM EXIT
  kill 0
}
trap cleanup SIGINT SIGTERM EXIT

(cd "$ROOT_DIR/backend" && uv run fastapi dev app/main.py) &
(cd "$ROOT_DIR/frontend" && npm run dev) &

wait
