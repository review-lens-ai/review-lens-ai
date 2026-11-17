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

echo "== API client: generated code matches the backend"
# Regenerate, then compare with the files as they were. A difference means someone
# changed a backend route or schema without running `npm run api:generate`.
GENERATED=(frontend/openapi.json frontend/src/lib/generated/api.ts)
BEFORE="$(mktemp -d)"
for file in "${GENERATED[@]}"; do cp "$ROOT_DIR/$file" "$BEFORE/$(basename "$file")"; done
(cd "$ROOT_DIR/frontend" && npm run --silent api:generate > /dev/null)
for file in "${GENERATED[@]}"; do
  if ! cmp -s "$BEFORE/$(basename "$file")" "$ROOT_DIR/$file"; then
    echo "$file was out of date. It is regenerated now: review and commit it." >&2
    exit 1
  fi
done

echo "== Frontend: lint"
(cd "$ROOT_DIR/frontend" && npm run lint)
echo "== Frontend: format"
(cd "$ROOT_DIR/frontend" && npm run format:check)
echo "== Frontend: types"
(cd "$ROOT_DIR/frontend" && npm run typecheck)
echo "== Frontend: tests"
(cd "$ROOT_DIR/frontend" && npm test)

echo "All checks passed."
