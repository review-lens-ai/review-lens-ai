# review-lens-ai

## Stack

- `frontend/` — Next.js (React 19, Tailwind 4)
- `backend/` — FastAPI, managed with [uv](https://docs.astral.sh/uv/)
- `supabase/` — database/auth backing both apps

## Prerequisites

- Node.js + npm
- [uv](https://docs.astral.sh/uv/getting-started/installation/)

## Setup

```bash
./scripts/setup.sh
```

Installs dependencies and creates `backend/.env` and `frontend/.env.local` from their `.example` files. Fill in the values before running (see below).

## Environment setup

1. Where to find values:
   - Supabase dashboard → Project Settings → API: project URL and publishable key.
   - Supabase dashboard → Project Settings → Database: connection string (`DATABASE_URL`).
   - Review provider's dashboard: `REVIEW_PROVIDER_API_KEY`.
2. Public vs secret:
   - `NEXT_PUBLIC_*` values are public; they are shipped to the browser. Never put a secret in one.
   - `DATABASE_URL` and all API keys are secret and live only in the backend environment.
3. Production and CI:
   - Production secrets go in the Vercel / Render environment settings, never in files.
   - CI secrets (`DATABASE_URL`, `REVIEW_PROVIDER_API_KEY`) go in GitHub Actions secrets.
4. The Google OAuth client secret is set only in the Supabase dashboard, never in a `.env` file.
5. `.env` files are never committed. The Supabase secret/service-role key is not used by this project (it bypasses RLS).

The backend fails at startup with `Missing required setting: <NAME>` if `SUPABASE_URL` or `DATABASE_URL` is missing.

## Running locally

```bash
./scripts/dev.sh
```

Starts the backend (http://localhost:8000) and frontend (http://localhost:3000) together. Ctrl+C stops both.
