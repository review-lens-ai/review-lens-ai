# Coding Standards

Rules for developers and AI coding tools.

Engineering philosophy is located in [Engineering Philosophy](philosophy.md).

## 1. Repository layout

```
review-lens-ai/
├── frontend/src/
│   ├── app/              # Pages, layouts, routes
│   │   └── _components/  # Reusable UI components (shadcn primitives in ui/)
│   ├── lib/              # API clients, utilities
│   └── proxy.ts          # Redirects signed-out users to /login
├── backend/
│   ├── app/
│   │   ├── main.py       # FastAPI entry point
│   │   ├── worker.py     # Background worker entry point
│   │   ├── auth.py       # Supabase token verification, ownership checks
│   │   ├── platforms.py  # Checks and reads the Maps link
│   │   ├── core/         # config.py (env vars, secrets), errors.py
│   │   ├── api/          # HTTP endpoints, mounted on router.py
│   │   ├── schemas/      # Request/response shapes
│   │   ├── repositories/ # All SQL, filtered by owner
│   │   ├── services/     # Processing logic
│   │   ├── adapters/     # External providers (SerpApi)
│   │   ├── db/           # Migration runner
│   │   └── fixtures/     # Recorded provider responses for tests
│   └── tests/            # api, auth, db, unit
├── supabase/migrations/  # Version-controlled SQL
├── docs/
│   ├── architecture/     # System design, diagrams (start at architecture.md)
│   └── standards/        # Coding, philosophy, AI-assisted development, guardrails, UI/UX
├── scripts/              # setup, dev, check, migrate, branch protection
└── .github/              # CI workflow, PR template
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
| Skipped record | `IngestionRejection` | `ingestion_rejections` |

### Code

- Schemas: `<Entity>Create`, `<Entity>Response`.
- Identifiers: camelCase; PascalCase for components and types; UPPER_CASE for constants.
  The exported component is PascalCase even though its file is kebab-case:
  `status-badge.tsx` exports `StatusBadge`.
- TypeScript files: kebab-case (`status-badge.tsx`, `api-client.ts`), enforced by the
  `check-file` lint rule. Shared components live in `src/app/_components/`
  (shadcn primitives in `src/app/_components/ui/`). `src/components/` is not used:
  the lint rule would require PascalCase file names there, and shadcn generates kebab-case.
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

- Lists: `{"items": [...]}`.
- A list that can grow without bound (e.g. reviews) is paginated: `?limit=&offset=`,
  default 50, max 100. The response adds `total`, `limit` and `offset`.
  The ordering ends with a unique column (`id`), so pages never overlap or skip a row.
- On a paginated list, `items` is one page, not everything. Clients read `total` to
  know there is more; a client that ignores it shows a partial list as if complete.
- Small lists (e.g. a user's targets) stay plain `{items}`. Two shapes is the
  tradeoff for paging only what needs it.
- Offset paging is a choice for this project's size, not a rule. Rows shift when
  new data arrives mid-paging, and deep offsets get slow. If a list needs infinite
  scroll or gets large, switch it to cursor paging (`?after=<last id>`).

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

TBD.
