# Coding Standards

Rules for developers and AI coding tools.

Engineering philosophy is located in [Engineering Philosophy](philosophy.md).

## 1. Repository layout

```
review-lens-ai/
├── frontend/src/
│   ├── app/              # Pages, layouts, routes
│   ├── components/       # Reusable UI components
│   └── lib/              # API clients, utilities
├── backend/
│   ├── app/
│   │   ├── main.py       # FastAPI entry point
│   │   ├── worker.py     # Background worker entry point
│   │   ├── core/         # config.py (env vars, secrets), errors.py
│   │   ├── auth.py       # Supabase token verification, ownership checks
│   │   ├── api/          # HTTP endpoints, mounted on router.py
│   │   ├── services/     # Processing logic
│   │   ├── adapters/     # External providers (SerpApi)
│   │   ├── schemas/      # Request/response shapes
│   │   └── fixtures/     # Recorded provider responses for tests
│   └── tests/
├── supabase/
│   ├── migrations/       # Version-controlled SQL
│   ├── config.toml       # Local Supabase config
│   └── seed.sql          # Optional dev data
├── docs/
│   ├── architecture/     # System design, diagrams
│   ├── standards/        # Git workflow, coding, AI-assisted development
│   └── decisions/        # Architecture Decision Records
└── .github/workflows/    # CI/CD
```

## 2. Naming

Follow each language's standard: PEP 8, common TypeScript conventions, Postgres snake_case. Ruff (`N` rules) and ESLint (`@typescript-eslint/naming-convention`, `eslint-plugin-check-file`) enforce what they can. Object properties are not checked, since JSON fields are snake_case (see API).

### Domain glossary

Use these terms everywhere. No synonyms.

| Concept | Code | Table |
|---|---|---|
| Thing being analyzed | `AnalysisTarget` | `analysis_targets` |
| One ingestion attempt | `IngestionRun` | `ingestion_runs` |
| Normalized review | `Review` | `reviews` |

### Code

- Schemas: `<Entity>Create`, `<Entity>Response`.
- Identifiers: camelCase; PascalCase for components and types; UPPER_CASE for constants.
  Components live in `src/components/`.
- TypeScript files: PascalCase for components (`TargetCard.tsx`), kebab-case otherwise (`api-client.ts`).
- Next.js routes: lowercase folders, private folders prefixed `_`, reserved file names only (`page.tsx`, `layout.tsx`).

### Database

- Tables plural, columns snake_case.
- Primary key `id`. Foreign keys `<singular>_id`.
- Timestamps `<event>_at`.
- Enum values UPPERCASE.
- Migrations: `<timestamp>_<action>_<object>.sql`, e.g. `20261023120000_create_analysis_targets.sql`.

### API

- Paths: `/api/v1` prefix, plural nouns, kebab-case.
- Query params and JSON fields: snake_case. Matches Python and the database, no conversion layer.
- Mount every router on `api_router` (`app/api/router.py`). It adds the `/api/v1` prefix and requires a verified user (S1-BR-004).
- Public endpoints (e.g. `/health`) live outside `api_router`.

### Environment variables

- UPPER_SNAKE_CASE.
- Browser-visible: `NEXT_PUBLIC_` prefix, public values only.
- Secrets: backend only, never `NEXT_PUBLIC_` (S1-BR-011).
- Source of truth: `backend/.env.example`, `frontend/.env.example`.

### Tests

- Files: `test_<module>.py`, `<Component>.test.tsx`.
- Names: `test_<subject>_<expected_result>`, e.g. `test_user_b_cannot_read_user_a_target`.
- Fixtures: `<provider>_<scenario>.json`.

### Git

- Branches, PRs: `<type>/<SNUM>-<description>`, e.g. `feat/S1-001-create-target`.
  A branch covering several stories lists them all: `feat/S1-006-S1-007-quality-and-ci`.
- Commits: `<type>/<description>`, e.g. `fix/regenerate npm lockfile`.
  Story IDs go in the body as `Refs: S1-006` (several allowed), not the subject.
- Types: feat, fix, test, chore, docs, ci.

## 3. Formatting and linting

- Frontend: ESLint + Prettier
- Backend: Ruff

## 4. Error handling

TBD.

## 5. API responses

TBD.

## 6. Logging

- Backend (FastAPI, worker): Python's standard `logging` module.
- Frontend: Next.js standard logging for now.
- Persist pipeline stage checkpoints (completion status, failure details) in the database.
- Never log credentials, secrets or sensitive user data.

## 7. Dependencies

New dependencies must be approved by the team. In the PR, link the official source and justify it.

- **Necessary:** no existing solution does the job.
- **Real:** verified against official documentation.
- **Secure:** no known vulnerabilities, actively maintained.
- **Established:** widely adopted, well supported.

## 8. Testing

### Frontend

Two kinds of tests. `npm test` runs both, and CI runs `npm test`.

| Kind | File name | Covers | Runs in | Run alone |
|---|---|---|---|---|
| Unit | `<module>.test.ts` | One function, e.g. `lib/ingestion-runs.ts` | Node | `npm run test:unit` |
| Integration | `<page>.integration.test.tsx` | A whole page: render it, click, check what it shows | jsdom (a pretend browser) | `npm run test:integration` |

Tools:

- **Vitest** runs the tests.
- **React Testing Library** renders a page and finds and clicks things the way a user would
  (by role and visible text, not by CSS class).
- **MSW** (Mock Service Worker) plays the backend: each test lists the endpoints the page
  calls and what they return. Nothing leaves the test.

Shared test code lives in `frontend/src/test/`. Use it instead of writing your own copy:

| File | What it gives you |
|---|---|
| `factories.ts` | `ingestionRun({...})`, `target({...})`: a valid object, with only the fields you pass changed |
| `responses.ts` | `jsonResponse(body, status)`, `apiErrorResponse(status, code, message)` |
| `server.ts` | The MSW `server` and the fake `API` base URL |
| `integration-setup.ts` | Runs around every integration test: starts MSW, clears the page and handlers after each test |
| `index.ts` | Everything above plus Testing Library and MSW, so an integration test has one import: `from "@/test"` |

When a backend type gains a field, add it to `factories.ts` once.

#### Writing an integration test

Example: `frontend/src/app/targets/[id]/page.integration.test.tsx`.

1. **Mock what the page uses but the test doesn't cover** with `vi.mock(...)` at the top of the
   file, e.g. `next/navigation` (router, URL params) and the Supabase client.
2. **Set up the backend** with `server.use(...)`, one handler per endpoint the page calls:

   ```ts
   server.use(
     http.get(`${API}/api/v1/targets/${TARGET_ID}`, () => jsonResponse(target({ id: TARGET_ID }))),
     http.post(`${API}/api/v1/targets/${TARGET_ID}/ingestion-runs`, () =>
       apiErrorResponse(409, "CONFLICT", "Already running."),
     ),
   );
   ```

   If the page calls an endpoint with no handler, the test fails and names the request.
   When several tests open the same page, put these handlers in one helper in the test file.
3. **Render, wait, act, check:** `render(<Page />)`, wait for it to load with
   `await screen.findBy...`, click with `fireEvent.click(...)`, then check what the user sees.
4. To change what the backend returns partway through a test, call `server.use(...)` again.
   The newest handler wins.

#### Limits

- Integration tests check the page with a fake backend. They don't check the real backend or
  the deployed app.
- They are tied to the whole page, so a change to an unrelated part of the page (its heading,
  another request) can break them. Logic that can be tested on its own belongs in a unit test.
