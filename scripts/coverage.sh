#!/usr/bin/env bash
# Changed-code coverage gate (S2-001): the lines a branch changes must be at least
# 80% covered by tests, checked separately for the backend and the frontend.
# CI runs it on pull requests into dev. Policy: docs/standards/coding-standards.md §8.
#
# Usage: TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres ./scripts/coverage.sh
# Compares against origin/dev; set COVERAGE_BASE to use another branch.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE="${COVERAGE_BASE:-origin/dev}"
THRESHOLD=80

# Without a database the db tests skip, so their code would look untested.
: "${TEST_DATABASE_URL:?Set TEST_DATABASE_URL to a throwaway Postgres, or the database tests skip and coverage looks low}"

cd "$ROOT_DIR"

echo "== Backend: tests with coverage"
(cd backend && uv run pytest -q --cov=app --cov-branch --cov-report=term --cov-report=xml:coverage.xml)

echo "== Frontend: tests with coverage"
(cd frontend && npx vitest run --coverage)

# The lcov report names files from frontend/ (src/...), git from the repo root
# (frontend/src/...). Without this, diff-cover matches no lines and always passes.
sed 's#^SF:src/#SF:frontend/src/#' frontend/coverage/lcov.info > frontend/coverage/lcov-root.info

echo "== Backend: changed lines vs $BASE (at least $THRESHOLD%)"
uv run --project backend diff-cover backend/coverage.xml \
  --compare-branch="$BASE" --src-roots backend --fail-under="$THRESHOLD"

echo "== Frontend: changed lines vs $BASE (at least $THRESHOLD%)"
uv run --project backend diff-cover frontend/coverage/lcov-root.info \
  --compare-branch="$BASE" --fail-under="$THRESHOLD"

echo "Changed-code coverage passed."
