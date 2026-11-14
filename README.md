# review-lens-ai

## Stack

- Frontend: Next.js, React 19, Tailwind 4
- Backend: FastAPI, managed with [uv](https://docs.astral.sh/uv/)
- Database and auth: Supabase

Folder layout and conventions: [Coding Standards](docs/standards/coding-standards.md).

## Prerequisites

- Node.js + npm
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

The backend runs on Python 3.12, the same version as CI (`backend/.python-version`). uv picks it up automatically and downloads it if it is missing, so no separate Python install is needed. A different version can change generated files (for example, Python 3.13 renames HTTP 422 in `frontend/openapi.json`) and make `./scripts/check.sh` fail.

Any Python 3.12 works, including the python.org installer. The backend trusts the [certifi](https://pypi.org/project/certifi/) certificate bundle for its HTTPS calls, so the installer's "Install Certificates.command" step is not needed. If sign-in succeeds but the app goes straight back to `/login`, the backend is answering 401: look for `token rejected` in the backend's output.

## Setup

```bash
./scripts/setup.sh
```

Installs dependencies and creates `backend/.env` and `frontend/.env.local` from their `.example` files. Each variable is described in its `.example` file.

Where to find values:

- Supabase dashboard → Project Settings → API: project URL and publishable key.
- Supabase dashboard → Project Settings → Database: connection string (`DATABASE_URL`).
- Review provider's dashboard: `REVIEW_PROVIDER_API_KEY`.

Production secrets go in the Vercel / Render environment settings. CI secrets go in GitHub Actions secrets. The Supabase service-role key is not used (it bypasses RLS).

The backend fails at startup with `Missing required setting: <NAME>` if `SUPABASE_URL` or `DATABASE_URL` is missing.

## Running locally

```bash
./scripts/dev.sh
```

Starts the backend (http://localhost:8000), the frontend (http://localhost:3000) and the ingestion worker (`python -m app.worker`, which processes queued review collections) together. Ctrl+C stops all three.

## Docs

- [Coding Standards](docs/standards/coding-standards.md)
- [Engineering Philosophy](docs/standards/philosophy.md)
- [AI-Assisted Development](docs/standards/ai-assisted-development.md)
- [Data and Security Guardrails](docs/standards/data-security-guardrails.md)

## Quality checks

```bash
./scripts/check.sh
```

Runs every check CI runs except the production build (`cd frontend && npm run build`), and stops at the first failure:

| Layer    | Lint                | Format                         | Types               | Tests          |
| -------- | ------------------- | ------------------------------ | ------------------- | -------------- |
| Backend  | `ruff check .`      | `ruff format --check .`        | n/a                 | `pytest`       |
| Frontend | `npm run lint`      | `npm run format:check`         | `npm run typecheck` | `npm test`     |

Fix formatting automatically with `ruff format .` (backend) and `npm run format` (frontend). Rules live in `backend/pyproject.toml` (`[tool.ruff]`) and `frontend/.prettierrc.json`, so results do not depend on anyone's editor. Tests never open a browser or call a live service.

Backend coverage (lines and branches) for any file: `cd backend && uv run pytest --cov=app.auth --cov-branch --cov-report=term-missing`.

Changed-code coverage gate (CI runs it on PRs into `dev`; at least 80% of changed lines covered, backend and frontend separately). Needs the test database:

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres ./scripts/coverage.sh
```

Policy and exclusions: [Coding Standards §8](docs/standards/coding-standards.md#8-testing).
