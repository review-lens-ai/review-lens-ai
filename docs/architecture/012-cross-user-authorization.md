# Cross-User Authorization

How ReviewLens makes sure a user can only read or change their own AnalysisTargets and the data under them (S1-BR-007 to S1-BR-010, S1-BR-016, S1-BR-018). Implemented in S1-011, S1-012 and S1-013.

## The rule

A user can act on an AnalysisTarget only if they own it. This is enforced on the backend. Hiding a target in the UI is not protection.

## How a request is checked

```
DELETE /api/v1/targets/<target_id>
  1. get_current_user      verifies the Supabase token (invalid -> 401)
  2. get_current_user_id   takes the user id from the token's `sub` claim
  3. TargetRepository      runs SQL that matches BOTH the target id and the owner id
  4. no row matched        NotFoundError -> 404
```

## Where each value comes from

| Value | Source | Trusted? |
|---|---|---|
| `target_id` | The URL | No. It only says which row the caller wants. |
| `owner_id` | The verified token (`sub`) | Yes. The only source of identity. |

The token proves **who is asking**. The `owner_id` column on the row says **who owns it**. The SQL compares the two:

```sql
delete from public.analysis_targets
 where id = %s and owner_id = %s
returning id
```

Request bodies and query strings are never used as an owner. Request schemas have no `owner_id` or `user_id` field.

## Why the check is in the SQL

Fetching a row and comparing owners in Python is two steps, and a forgotten comparison leaks data. With the owner in the `where` clause, another user's row is never loaded or changed.

## Why 404, not 403

A 403 confirms that the id exists. A 404 looks the same as an id that does not exist, so ids cannot be probed.

## Why the repository filters, and what RLS does

The backend connects to Postgres as the `postgres` role, which bypasses Row-Level Security. The owner filter in `app/repositories/targets.py` is therefore the real protection, and it is the only place that queries `analysis_targets`.

RLS is enabled on every table with no policies, and all privileges are revoked from `anon` and `authenticated`. That blocks the browser from reaching tables through Supabase's Data API. It is a backstop, not the main control.

## Child resources: runs and reviews (S1-013)

Ingestion runs, reviews and rejections have no owner column. They belong to whoever owns their target, so every route under `/api/v1/targets/{target_id}` checks the target's owner first:

| Route | Owner check | Another user gets |
|---|---|---|
| `GET /targets/{id}/reviews` | `ReviewRepository.list_for_target` → `get_owned` | 404, no review data |
| `POST /targets/{id}/ingestion-runs` | `IngestionRunRepository.create_run` → `get_owned` | 404, no run queued |
| `GET /targets/{id}/ingestion-runs/latest` | `IngestionRunRepository.latest_for_target` | 404 |
| `GET` / `DELETE /targets/{id}` | `TargetRepository` (`where owner_id = %s`) | 404, target unchanged |

No route writes reviews directly. Reviews are created only by the worker, for a run the owner started, and `bulk_insert` / `save_rejections` check the owner again and require the run to be RUNNING. So user B cannot create reviews under user A's target: B's start request is refused, nothing is queued, and the worker has nothing to run.

## Rules for new code

- Take the owner from `get_current_user_id`. Never from the request.
- Filter every query on user-owned data by `owner_id`.
- Runs, reviews and rejections are owned through their target. Any write to them must first load the target with `get_owned(target_id, owner_id)`.
- Return `NotFoundError` for a row that is missing or not owned.

## Tests

- `tests/db/test_target_access.py`: against a real Postgres. User B gets 404 on read and delete, the row is re-read from the database and is unchanged, the owner can read and delete, and no token gives 401.
- `tests/db/test_targets_endpoints.py`: the owner stored in the database is the token's user.
- `tests/api/test_delete_target.py`: the same rules against an in-memory repository.
- `tests/db/test_cross_user_reviews_and_ingestion.py` (S1-013):
  - A sweep of every route under `/api/v1/targets/{target_id}`: user B gets 404 with no data, and user A's target, runs and reviews are unchanged. New routes are covered automatically.
  - User B starting ingestion on user A's target: 404, nothing queued, the provider never called, no reviews created.

The database tests need `TEST_DATABASE_URL` pointing at a throwaway Postgres. Without it they are skipped, not failed:

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
```

## Known limits

- A malformed target id returns 422 (validation), not 404.
- A new route that needs a request body gets 422 instead of 404 in the S1-013 sweep until the sweep sends it a body.
