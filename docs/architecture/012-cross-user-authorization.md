# Cross-User Authorization

How ReviewLens makes sure a user can only read or change their own AnalysisTargets (S1-BR-007 to S1-BR-010, S1-BR-016). Implemented in S1-011 and S1-012.

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

## Rules for new code

- Take the owner from `get_current_user_id`. Never from the request.
- Filter every query on user-owned data by `owner_id`.
- Runs, reviews and rejections are owned through their target. Any write to them must first load the target with `get_owned(target_id, owner_id)`.
- Return `NotFoundError` for a row that is missing or not owned.

## Tests

- `tests/db/test_target_access.py`: against a real Postgres. User B gets 404 on read and delete, the row is re-read from the database and is unchanged, the owner can read and delete, and no token gives 401.
- `tests/db/test_targets_endpoints.py`: the owner stored in the database is the token's user.
- `tests/api/test_delete_target.py`: the same rules against an in-memory repository.

CI runs the database tests on every pull request against a throwaway Postgres service. To run them locally, point `TEST_DATABASE_URL` at a throwaway Postgres; without it they are skipped, not failed:

```bash
TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
```

## Known limits

- A malformed target id returns 422 (validation), not 404.
