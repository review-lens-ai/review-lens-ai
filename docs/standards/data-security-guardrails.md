# Data and Security Guardrails

**Story:** S1-003 — Create Data and Security Guardrails Context Document
**Rules:** S1-BR-001 to S1-BR-011
**Audience:** every developer on the team, and any AI coding tool generating backend, database or frontend code.

ReviewLens is multi-user. Every user-owned read and write must stay inside the signed-in user's data. This document says who a user is, what they own, where that is enforced, and what is never allowed.

## 1. Identity provider

- **Supabase Auth**, email and password. Users self-register, sign in, sign out and (once built) reset their password through Supabase.
- There is no Google sign-in. Google Maps is only where the reviews come from.
- Registering with an email that already has an account must not create a second account (spec 4.1).
- Sign-up and sign-in happen on `/login` (`frontend/src/app/login/page.tsx`), which calls Supabase directly through the browser client (`frontend/src/lib/supabase/client.ts`). Sign-up sends a verification link to the user's email; they can sign in only after clicking it, so the page asks them to check their email.
- On sign-in the browser receives a JWT access token (and a refresh token), kept in the Supabase session.
- The browser sends that token to the backend as `Authorization: Bearer <token>` on every call (`frontend/src/lib/api.ts`).
- The backend verifies it in `backend/app/auth.py`:
  - signature checked against Supabase's public keys, fetched from `<SUPABASE_URL>/auth/v1/.well-known/jwks.json` and cached;
  - asymmetric algorithms only (`RS256`, `ES256`); `none` and `HS256` are rejected. The Supabase project must therefore use asymmetric JWT signing keys, not the legacy shared JWT secret;
  - audience `authenticated`, issuer `<SUPABASE_URL>/auth/v1`, `exp` and `sub` required;
  - 30 seconds of clock-skew tolerance (PR #37).
- A missing, forged, expired or malformed token returns **401** with only `{"error": {"code", "message"}}` and a `WWW-Authenticate: Bearer` header. Only the error type (for example `ExpiredSignatureError`) is logged on the server; the token itself is never logged or sent back.
- The frontend treats a 401 from the backend as signed out and sends the user to `/login`.

## 2. How identity maps to ownership

```
JWT "sub"  =  auth.users.id  =  public.users.id  ←  analysis_targets.owner_id
```

- `public.users` mirrors Supabase Auth accounts. A database trigger (`on_auth_user_created`) inserts the row when someone signs up, so every verified `sub` has a matching user row.
- `get_current_user_id` in `backend/app/auth.py` (added with S1-015) returns the verified `sub`. **It is the only source of ownership.**
- Every new user-owned record takes its owner from `get_current_user_id`, never from the request.

## 3. User-scoped entities

| Entity | Table | Owned through |
| --- | --- | --- |
| User | `users` | Is the owner |
| AnalysisTarget | `analysis_targets` | `owner_id` (direct) |
| IngestionRun | `ingestion_runs` | `target_id` → target's `owner_id` |
| Review | `reviews` | `target_id` and `run_id` → target's `owner_id` |
| Ingestion rejection | `ingestion_rejections` | `run_id` → run → target's `owner_id` |
| Ingestion stage | `ingestion_stages` | `run_id` → run → target's `owner_id` |
| Raw provider page | `ingestion_raw_pages` | `run_id` → run → target's `owner_id` |

- Ownership is transitive: runs, reviews, rejections, stages and raw pages are reachable only through a target the user owns.
- `reviews` uses a composite foreign key `(run_id, target_id)`, so a review can never point at another target's run.
- Deleting a user cascades to their targets and everything under them.

## 4. Where authorization is enforced

Three layers. Only the second one actually protects data from our own backend.

| Layer | Where | What it does |
| --- | --- | --- |
| 1. Authentication | `api_router` in `backend/app/api/router.py` | Every `/api/v1` route requires a verified token (S1-BR-004). Public routes such as `/health` live outside it. |
| 2. Ownership filter | Repositories in `backend/app/repositories/` | Every query on user-owned data filters by `owner_id` in SQL (S1-BR-009). |
| 3. Data API lockout | Every table in the `public` schema | Row Level Security on with no policies, and all grants revoked from `anon` and `authenticated`. Today that is `users`, `analysis_targets`, `ingestion_runs`, `reviews`, `ingestion_rejections`, `ingestion_stages` and `ingestion_raw_pages`. |

### Why we don't use per-user RLS policies

RLS is on for every table in the `public` schema, but it locks out Supabase's public Data API, not our backend. We deliberately do **not** write per-user policies such as `auth.uid() = owner_id`. Here is why.

**RLS filters by who is connected to the database.** A per-user policy only works when the user's own token reaches Postgres, which is what happens when a browser queries tables directly with `supabase.from(...)`. Our browser never does that. Every request goes:

```
Browser → FastAPI → Postgres
```

FastAPI connects with one login for everyone: the `postgres` role in `DATABASE_URL`. That role owns the tables, and Postgres lets table owners skip RLS. So a policy like `auth.uid() = owner_id` would have no user to check and would never run for our backend's queries.

**That is why layer 2 is the one that matters.** The owner filter in our repositories is the only thing that keeps one user's rows away from another's. If a repository query forgets `AND owner_id = %s`, Postgres returns the row anyway.

**Why layer 3 still exists.** Supabase also exposes every table in the `public` schema through its auto-generated Data API. The publishable key is public (it ships in our frontend), so anyone can call that API. Those requests run as the `anon` or `authenticated` roles, and RLS *does* apply to them. RLS on with **no** policies means "deny every row" for those roles, and the `REVOKE` removes their table permissions. Supabase grants new tables in `public` to `anon` and `authenticated` by default, so every migration that creates a table in `public` must also enable RLS and `REVOKE` in the same file. Together they make that side door read and write nothing.

The Data API only reaches the `public` schema. The migration script's own bookkeeping table, `supabase_migrations.schema_migrations`, lives in a different schema, is not exposed, and is not locked the same way.

**Why we chose this design.**

- **One path to the data.** All access goes through FastAPI, which also validates input, calls the review provider and will build LLM prompts. Authorization lives in one place, in Python, covered by User A / User B tests.
- **Per-user policies would not run** while the backend connects as `postgres`, so writing them would give a false sense of safety.

**The trade-off.** The database has no safety net if someone writes an unfiltered query. We cover that with the repository rules below and the cross-user tests in section 8.

**If we want both layers later.** FastAPI could connect as a role that does not bypass RLS, pass each request's user id to Postgres, and add real per-user policies. Then a forgotten filter would also be caught by the database. It costs extra setup on every request and is not required by the spec. It is a candidate for Sprint 3 security hardening, and it would need its own reviewed migration.

**Rules for repositories:**

- All SQL for a table lives in its repository. Endpoints never write SQL.
- Reading or changing one record: `WHERE id = %s AND owner_id = %s`, as in `TargetRepository.get_owned`.
- Child records (runs, reviews): load the parent target with `get_owned(target_id, user_id)` first, then query the children.
- Someone else's record raises `NotFoundError` (**404**), exactly like a missing one. Never 403: a different answer would confirm the id exists (S1-BR-008).

**The frontend guard is a convenience only.** `frontend/src/proxy.ts` (PR #37) checks the session with Supabase's `getClaims()`, which verifies the token's signature, and redirects signed-out visitors to `/login` (S1-BR-003). Every page is protected unless it is on the proxy's public list. Logging out signs out of Supabase and sends the user to `/login` (S1-BR-006). After that:

- Loading any protected page sends the user to `/login`.
- Every backend call is re-checked, so even an old page still in the browser cannot load fresh data without a valid token.

## 5. Why client-supplied owner ids are never trusted

Anything in a request body, query string or header other than the verified token can be changed by the caller. If the backend used a client-sent `owner_id`, User A could create or read data as User B (S1-BR-010).

- Request schemas have **no** `owner_id` or `user_id` field. `TargetCreate` uses `extra="ignore"`, so any such key a client adds is dropped.
- The platform is fixed by the backend (`backend/app/platforms.py`); the client cannot choose it either.
- Validation runs on the backend. Frontend checks are for usability only, because anyone can call the API directly.
- Validation errors (422) are built from our own messages and never echo the submitted value.

## 6. Secrets and environment configuration

| Value | Where it lives | Secret? |
| --- | --- | --- |
| `SUPABASE_URL` | `backend/.env` | No |
| `DATABASE_URL` (the `postgres` role) | `backend/.env`, GitHub Actions secrets, Render | **Yes** |
| `REVIEW_PROVIDER_API_KEY` (SerpApi) | `backend/.env`, Render | **Yes** |
| `OPENROUTER_API_KEY` | `backend/.env`, Render | **Yes** |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`, `NEXT_PUBLIC_API_URL` | `frontend/.env.local`, Vercel | No, public by design |
| Supabase service-role key | **Not used** | It bypasses RLS |
| `TEST_DATABASE_URL` | Set in your terminal when running database tests, e.g. `postgresql://postgres:postgres@localhost:5432/postgres` | No: a throwaway local database with fake data |

`TEST_DATABASE_URL` tells the database tests which database to use. Without it they are skipped. It must never point at the real Supabase project, because tests create and delete rows; the tests refuse any address containing `supabase.co`.

Rules (S1-BR-011):

- Secrets are backend-only. Never put a secret in a `NEXT_PUBLIC_` variable; those are sent to the browser.
- `backend/.env.example` and `frontend/.env.example` are the source of truth for variable names. Real `.env` files are never committed.
- Production secrets go in Vercel and Render settings; CI secrets in GitHub Actions secrets.
- The backend fails at startup with `Missing required setting: <NAME>` if `SUPABASE_URL` or `DATABASE_URL` is missing. The message names the variable, never its value.
- Secret settings are `SecretStr`, so they are masked in logs and `repr`.
- Never log tokens, passwords, connection strings or API keys. Raw provider or exception text goes to server logs only; users and the database get a short fixed error code (for example `ErrorCode` in `ingestion_runs.py`).
- AI coding tools get the code and docs, never live data or environment values.

## 7. Untrusted input and AI context

Everything that comes from outside the backend is untrusted: request bodies, URLs users paste, and **review text from the source platform**. Reviews are written by strangers and can contain anything, including text that looks like instructions.

- Validate request input on the backend (`backend/app/schemas/`, `backend/app/platforms.py`). Only `https` Google Maps links are accepted as a target source.
- Store review text as data. Never build SQL from it (always use query parameters) and never render it as HTML.
- **Q&A context comes only from the active target's reviews**, loaded through a target the caller owns (`get_owned`). One user's reviews must never reach another user's AI context or answers (spec 4.1 and 4.4).
- When review text is sent to the LLM, it goes in as quoted data, separate from the system prompt. Text inside a review never changes the assistant's scope or rules.
- Never send secrets, tokens or other users' data to the LLM provider.

## 8. Testing cross-user isolation

Every endpoint that reads or writes user-owned data needs tests with **two users**. Name them `test_<subject>_<expected_result>`, for example `test_user_b_cannot_read_user_a_target`.

Minimum cases per user-owned endpoint:

1. No token → 401, and nothing is written.
2. User A can read or change their own record.
3. User B gets **404** for User A's record, the same response as a random id. Cover every operation the endpoint offers: read, update, delete, starting ingestion, and asking Q&A questions about the target (spec 4.1: User A cannot "read, modify, delete, ingest into, or query" User B's data).
4. A list endpoint returns only the caller's records.
5. A create request carrying another user's `owner_id` / `user_id` / `userId` is saved under the caller (see `backend/tests/db/test_target_ownership.py`).
6. A failed or forbidden request changes nothing in the database.

How to run them:

- API tests use a fake signing key (`backend/tests/conftest.py`): `make_token("user-a")` builds a valid token without calling Supabase.
- Database tests need `TEST_DATABASE_URL` pointing at a **throwaway local Postgres**. They refuse to run against `supabase.co`, and they skip when the variable is not set.
- `test_every_api_route_requires_a_token` walks the OpenAPI spec, so a new route that forgets authentication fails automatically.

PRs that change authentication, authorization, secrets or database permissions must include a security report: each risk, and the test that covers it ([AI-Assisted Development](ai-assisted-development.md)).

## 9. Never do this

- Query tables from the browser (`supabase.from(...)`). The browser only calls FastAPI.
- Write a query on user-owned data without an `owner_id` filter, or a child query without loading its target through `get_owned` first.
- Take ownership from the request body, query string or a header other than the verified token.
- Return 403, or a different message, for another user's record. Use 404.
- Add a `/api/v1` route outside `api_router`.
- Create a table without enabling RLS and revoking `anon` / `authenticated` in the same migration.
- Add an RLS policy or `GRANT` for the browser without its own reviewed migration.
- Change the schema in the Supabase dashboard. Every change is a file in `supabase/migrations/`.
- Use the Supabase service-role key.
- Put a secret in a `NEXT_PUBLIC_` variable, a commit, a log line or an error message.
- Point tests at the real Supabase project.
- Put another target's or another user's reviews into a Q&A prompt, or let review text change the assistant's instructions.