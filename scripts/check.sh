#!/usr/bin/env bash
# Runs every lint, format, type and unit-test check, the same ones CI will run.
# Stops at the first failure. Run it before opening a pull request.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== Backend: lint"
(cd "$ROOT_DIR/backend" && uv run ruff check .)
echo "== Backend: format"
(cd "$ROOT_DIR/backend" && uv run ruff format --check .)
echo "== Backend: tests"
(cd "$ROOT_DIR/backend" && uv run pytest -q)

echo "== Frontend: lint"
(cd "$ROOT_DIR/frontend" && npm run lint)
echo "== Frontend: format"
(cd "$ROOT_DIR/frontend" && npm run format:check)
echo "== Frontend: types"
(cd "$ROOT_DIR/frontend" && npm run typecheck)
echo "== Frontend: tests"
(cd "$ROOT_DIR/frontend" && npm test)

echo "All checks passed."
