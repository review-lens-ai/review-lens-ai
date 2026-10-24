# review-lens-ai

## Stack

- Frontend: Next.js, React 19, Tailwind 4
- Backend: FastAPI, managed with [uv](https://docs.astral.sh/uv/)
- Database and auth: Supabase

Folder layout and conventions: [Coding Standards](docs/standards/coding-standards.md).

## Prerequisites

- Node.js + npm
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Setup

```bash
./scripts/setup.sh
```

Installs dependencies and creates `backend/.env` and `frontend/.env.local` from their `.example` files. Each variable is described in its `.example` file.

Where to find values:

- Supabase dashboard → Project Settings → API: project URL and publishable key.
- Supabase dashboard → Project Settings → Database: connection string (`DATABASE_URL`).
- Review provider's dashboard: `REVIEW_PROVIDER_API_KEY`.

Production secrets go in the Vercel / Render environment settings. CI secrets go in GitHub Actions secrets. The Google OAuth client secret is set only in the Supabase dashboard. The Supabase service-role key is not used (it bypasses RLS).

The backend fails at startup with `Missing required setting: <NAME>` if `SUPABASE_URL` or `DATABASE_URL` is missing.

## Running locally

```bash
./scripts/dev.sh
```

Starts the backend (http://localhost:8000) and frontend (http://localhost:3000) together. Ctrl+C stops both.

## Docs

- [Coding Standards](docs/standards/coding-standards.md)
- [Engineering Philosophy](docs/standards/philosophy.md)
- [AI-Assisted Development](docs/standards/ai-assisted-development.md)
