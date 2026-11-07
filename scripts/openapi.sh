#!/usr/bin/env bash
# Prints the backend's OpenAPI spec (the input for the generated frontend client).
# Usage: ./scripts/openapi.sh > openapi.json
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# The spec does not depend on settings; placeholders let the app import without a .env.
cd "$ROOT_DIR/backend"
SUPABASE_URL=https://placeholder.supabase.co \
DATABASE_URL=postgresql://placeholder@localhost/placeholder \
  uv run python -c "import json; from app.main import app; print(json.dumps(app.openapi(), indent=2))"
