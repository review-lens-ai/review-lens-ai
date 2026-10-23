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

Installs dependencies and creates `backend/.env` and `frontend/.env.local` from their `.example` files. Fill in the values before running.

## Running locally

```bash
./scripts/dev.sh
```

Starts the backend (http://localhost:8000) and frontend (http://localhost:3000) together. Ctrl+C stops both.
