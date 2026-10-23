#!/usr/bin/env bash
# One-time local setup: installs dependencies and creates env files.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "Installing frontend dependencies..."
(cd "$ROOT_DIR/frontend" && npm install)

echo "Installing backend dependencies..."
(cd "$ROOT_DIR/backend" && uv sync)

if [ ! -f "$ROOT_DIR/backend/.env" ]; then
  cp "$ROOT_DIR/backend/.env.example" "$ROOT_DIR/backend/.env"
  echo "Created backend/.env — fill in values before running."
fi

if [ ! -f "$ROOT_DIR/frontend/.env.local" ]; then
  cp "$ROOT_DIR/frontend/.env.example" "$ROOT_DIR/frontend/.env.local"
  echo "Created frontend/.env.local — fill in values before running."
fi

echo "Setup complete. Run ./scripts/dev.sh to start the app."
