# Review Ingestion: Fetching Reviews

**Story:** S1-020 — Extract or Import Review Data
**Rules:** S1-BR-019 to S1-BR-021, S1-BR-024
**Code:** `backend/app/worker.py` (the pipeline) and `backend/app/adapters/serpapi/` (the SerpApi calls)

ReviewLens collects reviews from **Google Maps** through **SerpApi's Google Maps Reviews API** (`engine=google_maps_reviews`). The app never shows sample or made-up reviews: if fetching fails, the run fails and says why.

## 1. Why SerpApi

The spec lets each team pick one public review platform and a practical way to read it. SerpApi returns Google Maps reviews as JSON, page by page, with text, rating, date and a stable review id. Scraping Google directly would need a headless browser and break whenever Google changes its pages.

## 2. The worker (`app/worker.py`)

The API never fetches reviews itself: a fetch takes many slow requests. S1-019's endpoint creates a QUEUED run and answers at once. A separate **worker process** picks queued runs up, one at a time:

```
cd backend && uv run python -m app.worker      # scripts/dev.sh starts it with the API and web app
```

```
claim the oldest QUEUED run                  → RUNNING                        ingestion_runs
INGESTION stage                                                               ingestion_stages
  1. find the place (free), save its id      → analysis_targets.source_place_id
  2. fetch_reviews(...)                      (the adapter, sections 3–7)
  3. save the pages                          → ingestion_raw_pages
  4. save place and run details              → analysis_targets / ingestion_runs .source_metadata
NORMALIZATION stage
  5. normalize_pages(stored pages)           (S1-021)
  6. save reviews and rejections             (S1-022's ReviewRepository)
  7. mark_succeeded(saved, rejected, dups)   → COMPLETED / PARTIAL / FAILED (NO_REVIEWS_FOUND)
```

Rules the worker keeps:

| Rule | How |
|---|---|
| Only one worker gets a run | `claim_next_queued` uses `FOR UPDATE SKIP LOCKED` |
| A stage is done only once its data is written | the stage's COMPLETED mark is committed together with its writes |
| A failed stage leaves nothing half-written | its writes are rolled back, the stage is marked FAILED with an `ErrorCode`, and the run fails with the same code |
| Keep what we paid for | pages fetched before a provider failure are still saved |
| The run's status matches the saved reviews | step 6, step 7 and NORMALIZATION's COMPLETED mark share one commit |
| A retry skips finished work | `requeue` keeps the stage rows; a COMPLETED INGESTION is skipped, so normalizing again costs no searches. A failed stage restarts from its beginning, replacing that run's earlier raw pages |
| No search spent on a duplicate place | the place id is resolved and saved (and committed) **before** the first search; if the owner already has a target for that place (`analysis_targets_owner_place_key`), the run fails with `SOURCE_UNREACHABLE` and no search is made |
| Counts add up | reviews an earlier run already saved, unchanged, or already rejected (e.g. rating-only, S3-009) count as duplicates; edited ones count as updated (S3-010), so fetched = saved + updated + rejected + duplicates |
| A failed refresh changes nothing | the older-review cursor moves in the NORMALIZATION commit with the reviews (S3-009), so a failed run leaves the reviews and the cursor as they were |
| Nothing internal leaks | only the `ErrorCode` is stored; the technical detail goes to the server log, scrubbed by `mark_failed` |
| One bad run never stops the worker | each pass opens its own connection; an error is logged and the loop goes on |

**Why a separate process.** A fetch holds a run for up to several minutes, so it cannot live inside an API request. A separate process also restarts on its own and never slows the API. On Render it would be a Background Worker, which may not be on the free plan; if so, the same `process_next_run` can be called from a background thread in the API process instead, with no other change.

**Until S1-022 lands.** The worker saves reviews through S1-022's `ReviewRepository` (`app/repositories/reviews.py`), expected to offer `bulk_insert(run_id, target_id, owner_id, reviews) -> int` (how many were new) and `save_rejections(...)`. Until that file exists, NORMALIZATION fails with `UNEXPECTED` instead of reporting reviews it did not save.

The adapter itself reads and writes no tables; the worker saves what it returns:

```
fetch_reviews(source_url, cap, data_id)  ──pages──►   normalize_pages(payloads)
  │
  ├─ result.data_id        → analysis_targets.source_place_id
  ├─ result.place_details  → analysis_targets.source_metadata
  ├─ result.pages          → ingestion_raw_pages (page_number, payload, next_page_token)
  └─ result.run_details    → ingestion_runs.source_metadata
```

A failure raises a `ReviewFetchError`. Its `error_code` goes to `mark_failed`, and its `pages` (fetched before the failure) are still saved.

## 3. How a fetch works

1. **Find the place.** SerpApi needs the place's `data_id` (e.g. `0x89c2537da9b3163d:0x2be30fcff506fec8`), not the link the user pasted.
   - Full place links contain it after `!1s` in their `data=` part, or in an `ftid=` parameter. It is read from the link, at no cost.
   - Short share links (`maps.app.goo.gl/...`) are followed one redirect at a time, **only to Google hosts**, until the full link appears. A link that leads anywhere else is refused, so a pasted link can never make the server call an arbitrary address.
   - Search links and `?cid=` links carry no `data_id` and are refused before any request.
   - `resolve_place(source_url)` does only this step, so the worker can save the id before spending a search. Later runs pass the saved id in and skip it.
2. **Fetch pages.** One request returns one page; `serpapi_pagination.next_page_token` asks for the next. The first page always has 10 reviews; later pages ask for up to 20 (`num`, which SerpApi refuses on the first page). Language: `hl=en`. Sort: `newestFirst`.
3. **Stop**, recording why (`StopReason`):

   | Reason | When |
   |---|---|
   | `CAP_REACHED` | `MAX_REVIEWS_PER_RUN` **new** reviews fetched (not seen by an earlier run). The last request asks only for the reviews still needed |
   | `SEARCH_LIMIT` | `MAX_SEARCHES_PER_RUN` searches used, whatever they found |
   | `CAUGHT_UP` | a refresh reached reviews the target has, and every older review was collected before (section 3a) |
   | `NO_MORE_PAGES` | the last page had no next-page token |
   | `EMPTY_PAGE` | a page came back with no reviews |
   | `REPEATED_TOKEN` | SerpApi gave a token already used in this run, which would loop forever |
   | `NO_RESULTS` | SerpApi says the place has no reviews |

4. **Clean every page before returning it:**
   - the API key is removed if it appears anywhere;
   - **reviewer identity is scrubbed** with S1-021's `scrub_page`, the same rule used for rejected records: no reviewer names, profile links, avatars, contributor ids, photos or owner replies. Reviewer activity stats (`local_guide`, `reviews`, `photos`) stay;
   - NUL characters and NaN/infinity are removed, so the page fits a `jsonb` column.

   Each review keeps its `review_id`, `rating`, `snippet`, `extracted_snippet` (original and translated text), date and other fields, so normalizing a stored page gives the same reviews as normalizing it straight away.

**No resume within a run.** A failed stage restarts from its beginning (pipeline design), so a retried INGESTION stage fetches from page 1 again. The worker replaces that run's earlier raw pages: `(run_id, page_number)` is unique.

## 3a. Refresh (S3-009)

A run on a target that already has reviews is a refresh. Same button, same endpoint, same target, a new `ingestion_runs` row. It does two things:

1. **New reviews.** Read newest first from page 1 until a page on which **every** review was seen before (saved or rejected). A page that mixes new and seen reviews does not count: that one extra page is a margin for reviews Google publishes late or out of date order.
2. **Older reviews.** Jump to `analysis_targets.older_page_token`, the token for the page after the oldest one any earlier run read, and keep reading until the cap.

Example, a place with 500 reviews and a cap of 100:

| Run | Reads | Searches | Dataset |
|---|---|---|---|
| First ingestion | reviews 1–100 | 6 | 100 |
| Refresh, nothing new | page 1 (all seen), then 101–200 | 6 | 200 |
| Refresh, 4 new | page 1 (4 new), page 2 (all seen), then the next older ones | 7 | 300 |
| … | until the last page; then `all_reviews_collected` | | 500 |
| Refresh after that | page 1 only (`CAUGHT_UP`) | 1 | 500 + new |

Rules:

- **Seen = saved or rejected.** `ReviewRepository.seen_review_ids_for_target` reads both `reviews` and `ingestion_rejections`. Rating-only reviews are rejected on every run, so without them a page holding one would never count as seen. A review without a `review_id` always counts as new.
- **The cursor** (`older_page_token`, `all_reviews_collected`) moves only when a run read older reviews, and only in the NORMALIZATION commit, with the reviews. A refresh that hits the cap before it reaches seen reviews leaves it alone.
- **A refused or empty answer to the saved token** (expired, unknown) does not fail the run: it walks down from page 1 instead (`resume_rejected`), past the seen pages, and saves the new cursor where it stops. `MAX_SEARCHES_PER_RUN` caps how far one walk goes; the next refresh continues from there.
- **Overlap is harmless.** New reviews push older ones down, so a saved token can re-read a few seen reviews. They are duplicates.
- **Nothing is deleted.** A review missing from a later response stays: SerpApi never confirms a complete dataset (S3-BR-015).
- **Duplicates (S3-011).** A review is the same source review when it has the same `review_id` within the target (unique key `reviews_target_id_source_review_id_key`), so a review returned by several refreshes has one row. A review without a `review_id` is always kept: nothing proves it is a duplicate, and SerpApi sends one with every review.
- **Edits (S3-010).** A saved review that comes back with new text, rating or edit date (`edited_at`) is updated in place, re-embedded, and counted in `ingestion_runs.reviews_updated`; `run_id` keeps the run that first saw it. Only reviews on the pages a refresh reads are checked, so an edit to an older review deeper down is not picked up.
- **Failures (S3-012).** A refresh saves its reviews, moves the cursor and finishes the run in one commit. If anything fails, all of it rolls back: the run is FAILED with an `ErrorCode`, and the reviews, the summary and the cursor are exactly as before. The workspace says "Your existing N reviews are unchanged."
- Targets ingested before S3-009 get a cursor from their newest finished run's last page (migration `20261123120000_add_older_review_cursor.sql`).

## 4. What a fetch returns (`FetchResult`)

| Field | Content |
|---|---|
| `data_id` | the place id used |
| `pages` | `RawPage(page_number, payload, next_page_token)` per page: one `ingestion_raw_pages` row each |
| `payloads` | the page payloads, for `normalize_pages` |
| `review_records` | every review across the pages, each identified by `review_id` |
| `place_details` | from page 1's `place_info`: `title`, `address`, `category`, `rating`, `review_count` |
| `run_details` | provider, engine, sort, language, `max_reviews`, `pages_planned`, `pages_fetched`, `searches_used`, `reviews_fetched`, `new_reviews_fetched`, `stopped_because`, `caught_up`, `resumed_older`, `resume_rejected`. The worker adds `known_reviews_before` and, when the cursor moves, `older_cursor` |
| `reviews_fetched`, `searches_used`, `stopped_because` | counts and stop reason |
| `new_reviews`, `caught_up`, `resumed_older`, `resume_rejected`, `older_cursor` | S3-009: what a refresh found, and where the next run continues (`None` = keep the saved cursor) |

`pages_planned` vs `pages_fetched` shows what a run is missing, as the `ingestion_runs.source_metadata` migration asks.

## 5. Cost and limits

One request = one SerpApi search, including an empty "no results" answer. Failed and errored searches are not counted ([pricing FAQ](https://serpapi.com/pricing)).

**SerpApi free plan, checked Nov 2, 2026: 250 searches a month, and at most 50 an hour,** per account.

Searches per run = `pages_planned(cap)` = 1 + ⌈(cap − 10) / 20⌉:

| `MAX_REVIEWS_PER_RUN` | Searches per run | Runs per month (one free account) |
|---|---|---|
| 100 (default) | 6 | 41 |
| 200 | 11 | 22 |
| 500 | 26 | 9 |
| 1000 | 51 | 4 |

A refresh costs the same at most, and 1 search when the target is fully collected and nothing is new (section 3a). `MAX_SEARCHES_PER_RUN` (default 15) caps any one run.

At 1000, one run needs more searches than the hourly limit allows, so it fails partway with `QUOTA_EXCEEDED`. A run of 51 slow requests could also outlast the 10-minute stale-run limit (`STALE_RUN_AFTER`): if the owner then queues the same target again, `create_run` marks the slow run FAILED (`TIMEOUT`) and the worker's late `mark_succeeded` is refused and rolled back. Set a lower value in `backend/.env` for development.

## 6. Failures

Every failure is a `ReviewFetchError` subclass. Its `error_code` is an `ErrorCode` from `app/repositories/ingestion_runs.py`, ready for `mark_failed`.

| Exception | When | `error_code` |
|---|---|---|
| `ReviewFetchTimeoutError` | SerpApi, or the short link, did not answer within 30 s | `TIMEOUT` |
| `ReviewQuotaExceededError` | HTTP 429: monthly searches used up, or hourly limit hit | `QUOTA_EXCEEDED` |
| `ReviewProviderError` | SerpApi server error, unreadable response, network failure, or any other SerpApi error | `SOURCE_UNREACHABLE` |
| `ReviewProviderConfigError` | `REVIEW_PROVIDER_API_KEY` missing, or SerpApi rejected it (HTTP 401/403) | `UNEXPECTED` |
| `UnsupportedSourceUrlError` | the link does not lead to a Google Maps place we can identify | `SOURCE_UNREACHABLE` |

A place with no reviews is **not** an error: the result has no pages. The worker then saves nothing, and `mark_succeeded(run_id, 0, ...)` ends the run FAILED with `NO_REVIEWS_FOUND`.

## 7. The API key

- It lives only in `REVIEW_PROVIDER_API_KEY` in `backend/.env` (and the deployment's settings). It never reaches the browser.
- SerpApi takes it as a URL parameter, so:
  - httpx's request logging, which prints full URLs at INFO level, is raised to WARNING by the adapter;
  - errors are raised `from None`, because httpx's own errors contain the request URL;
  - provider messages are cleaned of the key before they go into an error;
  - the key is removed from every returned page.
- Tests check each of these.

## 8. Testing

`backend/tests/unit/serpapi/` never calls SerpApi or Google: every request goes to a fake transport (`httpx.MockTransport`).

- Two **recorded real pages** (Smashburger, Newark, newest first) from the SerpApi spike are the fixtures: `backend/app/fixtures/serpapi_reviews_with_missing_text.json` (also used by S1-021) and `serpapi_reviews_next_page.json`. Reviewer identities were replaced with fakes by the spike; four staff first names inside one review's text are replaced with `[name]`.
- Error responses follow SerpApi's [status and error codes page](https://serpapi.com/api-status-and-error-codes); they were not recorded live.
- A handoff test feeds the fetched pages into S1-021's `normalize_pages`: 18 reviews kept, 12 rating-only rejected, and fetched = kept + rejected + duplicates.
- `backend/tests/unit/worker/` runs the worker with the real adapter and in-memory repositories: the order of steps, commits and rollbacks, retries, and every failure code.
- `backend/tests/db/ingestion_worker/` runs it against Postgres (set `TEST_DATABASE_URL`, as for the other database tests), with only SerpApi and S1-022's review store faked.

## 9. Not covered yet

- `?cid=` and search links (they need a paid lookup to find the place).
- Retries inside a fetch after a timeout or server error (the whole stage is retried instead, when the user retries the run).
- Failing runs left RUNNING by a crashed worker: they are failed as stale only when the owner next queues that target (`create_run`).
- Deploying the worker (section 2).
- Google's cookie-consent redirect, which servers in the EU can get when following short links.
- The spike's plan of several sort orders per run (lowest-rated, newest, most relevant).
- Not yet confirmed against live SerpApi: short-link redirects, the last page having no next-page token, `review_id` staying the same across runs, and a `next_page_token` still working days later (section 3a falls back to walking down if it does not).
- More than `MAX_REVIEWS_PER_RUN` new reviews between two refreshes: the refresh stops at the cap before reaching seen reviews, leaving a gap between them that no run fills.
- Edits to reviews on pages a refresh does not read (section 3a).
