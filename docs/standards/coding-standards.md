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
│   │   ├── api/          # HTTP endpoints
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

Follow each language's standard: PEP 8, common TypeScript conventions, Postgres snake_case. Ruff (`N` rules) and ESLint (`naming-convention`) enforce what they can.

### Domain glossary

Use these terms everywhere. No synonyms.

| Concept | Code | Table |
|---|---|---|
| Thing being analyzed | `AnalysisTarget` | `analysis_targets` |
| One ingestion attempt | `IngestionRun` | `ingestion_runs` |
| Normalized review | `Review` | `reviews` |

### Code

- Schemas: `<Entity>Create`, `<Entity>Response`.
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

- Branches, PRs: `<type>/<SNUM><gh issue><description>`, e.g. `feat/S1-001--create-target`.
- Commits: `<type>/<description>`.

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

TBD.
