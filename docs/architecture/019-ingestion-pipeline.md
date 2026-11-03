# Ingestion Pipeline

> **Draft.** Covers S1-019, S1-020 and S1-022 while they are being built. It will be
> rewritten once all three are done.

How a review source becomes stored reviews: the user starts a run, a background
worker fetches and normalizes the reviews, and the workspace shows the result.
Rules: S1-BR-018 to S1-BR-025.

## Components

| Component | Responsibility | Code |
|---|---|---|
| Workspace page | Starts a run, polls its status, shows the badge and failure banner | `frontend/src/app/targets/[id]/page.tsx` |
| Ingestion API | Checks auth and ownership, queues the run, reports the latest run | `backend/app/api/ingestion_runs.py` |
| Run repository | Run lifecycle: create, start, finish, fail, retry, stale timeout | `backend/app/repositories/ingestion_runs.py` |
| Worker | Picks up queued runs and runs their stages | `backend/app/worker.py` |
| SerpApi adapter | Turns the Maps link into a place id and fetches review pages | `backend/app/adapters/serpapi/` |
| Normalizer | Raw records → reviews, rejections, duplicates | `backend/app/services/normalize_reviews.py` |
| Review repository | Saves reviews and rejections, reads a target's reviews | `backend/app/repositories/reviews.py` |

## Flow of one run

✅ = built · 🔨 = still to build (markers are temporary)

```
 BROWSER (workspace)            API  (FastAPI)                  DATABASE (Supabase)          WORKER (background)
 ───────────────────            ──────────────                  ───────────────────          ───────────────────
 [Start ingestion] ✅ ──POST /api/v1/targets/{id}/ingestion-runs ✅──┐          S1-019
                                │ verified token?        ✅ 401 if not
                                │ target exists + yours? ✅ get_owned → 404
                                │ source_url present?    🔨
                                │ create_run()           ✅ ──► ingestion_runs: QUEUED
                                │   (one active run per target ✅ → 409)
 badge: QUEUED ◄── 202 {run} ───┘
      │
      │ polls GET …/ingestion-runs/latest every few s ✅ (while QUEUED/RUNNING)
      ▼                                                                       ┌─ loop: pick up oldest QUEUED 🔨
 badge: RUNNING ◄────────────────────────────────── status = RUNNING ◄────────┤   mark_running() ✅
                                                                              │
                                    S1-020  ─────────────────────────────────►│ STAGE 1: INGESTION 🔨
                                    source_place_id ◄─────────────────────────┤  · link → place id (follow short links)
                                    ingestion_raw_pages ◄─────────────────────┤  · SerpApi pages (adapter, key from env)
                                     (scrubbed: no names)                     │  · scrub names, save each page
                                    ingestion_stages: INGESTION=COMPLETED ◄───┤  · mark stage done AFTER the writes
                                                                              │
                                    S1-021 ✅ + S1-022 ──────────────────────►│ STAGE 2: NORMALIZATION
                                                                              │  · normalize_pages(raw) ✅
                                                                              │      → reviews, rejected, duplicates
                                    reviews ◄─────────────────────────────────┤  · save reviews 🔨 (skip repeats of
                                    ingestion_rejections ◄────────────────────┤    the same source_review_id)
                                    ingestion_stages: NORMALIZATION=COMPLETED ◄┤  · save rejections 🔨, stage done
                                                                              │
 badge: Ingested / Partial / Failed ◄── status + counts ◄─────────────────────┤ mark_succeeded(saved, rejected, dupes) ✅
 failure banner ✅                                                             │  saved = 0 → FAILED / NO_REVIEWS_FOUND ✅
                                                                              │ any error → mark_failed(CODE) ✅
 [Retry] (later) ──► requeue() ✅ → back to QUEUED, skips completed stages    └─ stuck > 10 min → TIMEOUT ✅
```

## Run states

```
QUEUED ──► RUNNING ──► COMPLETED
  ▲ │          │   └──► PARTIAL   (some records skipped)
  │ │          └──────► FAILED
  │ └─────────────────► FAILED    (could not start, or stale)
  └──── requeue() ◄──── FAILED    (retry: same run, completed stages kept)
```

COMPLETED and PARTIAL are final. A target has at most one QUEUED or RUNNING run.

## API

| Method | Path | Result |
|---|---|---|
| `POST` | `/api/v1/targets/{target_id}/ingestion-runs` | **202** with the QUEUED run. 401 without a token, 404 for a missing or another user's target (nothing is queued), 409 if a run is already active. |
| `GET` | `/api/v1/targets/{target_id}/ingestion-runs/latest` | The newest run, or `null` if the target was never ingested. 404 for another user's target. |
| `GET` | `/api/v1/targets/{target_id}/reviews` | The target's stored reviews. 404 for another user's target. |

## What each story builds

| Story | Builds | Lives in | Status |
|---|---|---|---|
| **S1-019** Submit source | Enable the button. Add `POST /api/v1/targets/{id}/ingestion-runs`, which checks auth, ownership and `source_url`, then `create_run`, and returns **202** with the QUEUED run. Another user's target fails here, before any work starts. | `api/ingestion_runs.py`, workspace page | ✅ Done (component tests for the button: #55) |
| **S1-020** Extract data | The **worker loop** (`worker.py` is still a stub) and the **SerpApi adapter**: link → place id, fetch pages, scrub names, save raw pages, mark the INGESTION stage done. Tests use the saved fixtures, never live SerpApi. Document the chosen approach. | `worker.py`, `adapters/serpapi/` | 🔨 To do |
| **S1-021** Normalize | `normalize_pages`: raw records → reviews, rejections, duplicates. | `services/normalize_reviews.py` | ✅ Done |
| **S1-022** Save reviews | **Save** the normalized reviews and rejections, mark the NORMALIZATION stage done, call `mark_succeeded`. Add `GET /api/v1/targets/{id}/reviews` so the tests can show "A's reviews don't show up under B's target". | `repositories/reviews.py`, `api/reviews.py` | 🔨 To do |
| **S1-023** Result state | Run lifecycle, polling, status badge, failure banner. | `repositories/ingestion_runs.py`, workspace page | ✅ Done |
| **S1-013** Cross-user auth | Two-user tests: B can't start, read or retry A's runs or reviews. | backend tests | 🔨 To do |

Build order: S1-019 → S1-020 with the worker → S1-022 → S1-013.

The `source_url` check needs no extra code: the database requires a non-blank
`source_url` on every target, so any target `create_run` finds already has one.

## Design rules

- **A stage is done only after its writes.** The data and the stage status are saved
  in the same transaction, so a crash never leaves a completed stage with missing
  data. This is what makes retry safe.
- **The API never does the slow work.** The `POST` returns as soon as the run is
  QUEUED. The worker does the rest, and the workspace polls for progress.
- **No fake data.** Ingestion never falls back to hard-coded sample reviews. If
  SerpApi fails, the run ends FAILED with an error code.
- **No fake success.** A run that saved no reviews ends FAILED / NO_REVIEWS_FOUND,
  never COMPLETED. The database enforces this too.
- **Counts add up.** fetched = saved + rejected + duplicates
  (Smashburger sample: 90 = 55 + 12 + 23).
- **No reviewer identity is stored.** Names, profile links, photos and owner replies
  are removed before anything is saved, including raw pages.
