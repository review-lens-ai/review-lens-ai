# Deployment

The app is live at https://review-lens-ai-roan.vercel.app.

| Part | Platform | Address |
|---|---|---|
| Frontend | Vercel | https://review-lens-ai-roan.vercel.app |
| API | Render web service (free) | https://review-lens-ai.onrender.com |
| Ingestion worker | Render background worker ($7/month) | None. It runs in the background. |
| Database and sign-in | Supabase, the same project we use for development | Supabase dashboard |

All three services deploy from the `main` branch. Merging to `main` deploys the new version.

## How each service is set up

**Vercel** builds from the `frontend/` folder with the default Next.js settings. It needs the three `NEXT_PUBLIC_*` variables from `frontend/.env.example`. These values are built into the page, so after changing one, redeploy.

**Render** builds the API and the worker from the `backend/` folder:

- Build: `pip install uv && uv sync --frozen --no-dev`
- API start: `uv run fastapi run app/main.py --host 0.0.0.0 --port $PORT`
- Worker start: `uv run python -m app.worker`

Both read their settings from one shared environment group with the variables from `backend/.env.example`, plus `APP_ENV=production` and `PYTHON_VERSION=3.12.8`. `DATABASE_URL` is Supabase's pooler connection string. `CORS_ORIGINS` is set on the API service to the Vercel address.

**Supabase** has the Vercel address as its Site URL (Authentication → URL Configuration), so sign-up emails link to the live site.

## Good to know

- Run new migrations yourself with `./scripts/migrate.sh` before merging code that needs them. Deploys do not run them.
- The free API goes to sleep after about 15 minutes without traffic. The first request after that takes up to a minute, so open the site a few minutes before a demo.
- The worker runs all the time and checks the database for queued runs every 5 seconds.
