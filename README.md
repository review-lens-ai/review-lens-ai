# review-lens-ai

## Environment setup

1. Copy the templates and fill in real values:
   - `backend/.env.example` → `backend/.env`
   - `frontend/.env.example` → `frontend/.env.local`
2. Where to find values:
   - Supabase dashboard → Project Settings → API: project URL and publishable key.
   - Supabase dashboard → Project Settings → Database: connection string (`DATABASE_URL`).
   - Review provider's dashboard: `REVIEW_PROVIDER_API_KEY`.
3. Public vs secret:
   - `NEXT_PUBLIC_*` values are public; they are shipped to the browser. Never put a secret in one.
   - `DATABASE_URL` and all API keys are secret and live only in the backend environment.
4. Production and CI:
   - Production secrets go in the Vercel / Render environment settings, never in files.
   - CI secrets (`DATABASE_URL`, `REVIEW_PROVIDER_API_KEY`) go in GitHub Actions secrets.
5. The Google OAuth client secret is set only in the Supabase dashboard, never in a `.env` file.
6. `.env` files are never committed. The Supabase secret/service-role key is not used by this project (it bypasses RLS).

The backend fails at startup with `Missing required setting: <NAME>` if `SUPABASE_URL` or `DATABASE_URL` is missing.
