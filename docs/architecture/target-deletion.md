# Deleting a Target

How ReviewLens deletes an AnalysisTarget and what happens to the data under it (S3-005, S3-BR-007, S3-BR-008).

## Decision: cascade delete

Deleting a target removes it and everything that belongs to it, in one statement, at once. There is no soft delete, trash or undo.

| Data | What happens | How |
|---|---|---|
| `analysis_targets` row | Deleted | `TargetRepository.delete_owned` |
| `ingestion_runs` | Deleted | FK `on delete cascade` to the target |
| `ingestion_stages`, `ingestion_raw_pages` | Deleted | FK `on delete cascade` to the run |
| `reviews` (and their embeddings) | Deleted | FK `on delete cascade` to the target |
| `ingestion_rejections` | Deleted | FK `on delete cascade` to the run |
| Q&A history (planned, S3-006) | Must be deleted too | Its table needs an FK `on delete cascade` to the target |

Any new table that belongs to a target must reference `analysis_targets (id) on delete cascade`, so this stays true without code changes.

### Why cascade, not soft delete

- **Matches what the user is told.** The dialog says the target and its reviews are removed and that this can't be undone. A hidden copy would make that untrue.
- **Nothing to filter.** With soft delete, every query in every repository would need `and deleted_at is null`. One missed filter would show deleted data. Cascade keeps the existing queries correct.
- **Already in place.** The foreign keys were set up this way in Sprint 1 (S1-012). `test_schema.py` checks the cascade.
- **No restore requirement.** No story asks for undo or recovery.

## Who can delete

Only the owner. The SQL matches both the target id and the owner id from the token (see [012-cross-user-authorization.md](012-cross-user-authorization.md)). Another user's target, or one that does not exist, is a 404 and nothing changes.

## Deleting while a run is active

Allowed. The run is deleted with the target. The worker catches a run that disappears mid-way, logs it and moves on to the next run (`process_run` and `_fail_run` in `app/worker.py`); it does not crash.

## In the app

- **Delete target** button in the workspace header opens a confirmation dialog that names the target and how many reviews go with it.
- Confirm: `DELETE /api/v1/targets/{id}`, then the sidebar list reloads, a "Target deleted." toast shows, and the page goes to the target list (replacing history, so Back does not return to a deleted target).
- A 404 on delete counts as deleted (e.g. it was deleted in another tab).
- Any other failure keeps the dialog open with the reason. Nothing is removed.

## Tests

| What | Where |
|---|---|
| Owner deletes; it is gone from the list, the target, its reviews and summary | `backend/tests/db/test_reopen_and_delete_target.py` |
| Deleting one user's target leaves another user's data | `backend/tests/db/test_reopen_and_delete_target.py` |
| Another user cannot delete; a missing token is rejected | `backend/tests/db/test_target_access.py`, `backend/tests/api/test_delete_target.py` |
| Runs, reviews and rejections go with the target | `backend/tests/db/test_schema.py` |
| Dialog, confirm, cancel, failure, already-deleted | `frontend/src/app/targets/[id]/page.integration.test.tsx` |
