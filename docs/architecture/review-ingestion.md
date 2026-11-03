# Review Ingestion: Fetching Reviews

**Story:** S1-020 — Extract or Import Review Data
**Rules:** S1-BR-019 to S1-BR-021, S1-BR-024
**Code:** `backend/app/worker.py` (the pipeline) and `backend/app/adapters/serpapi.py` (the SerpApi calls)

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
| Counts add up | reviews `bulk_insert` skips because an earlier run already saved them count as duplicates, so fetched = saved + rejected + duplicates |
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
   | `CAP_REACHED` | `MAX_REVIEWS_PER_RUN` reviews fetched. The last request asks only for the reviews still needed, so the cap is exact |
   | `NO_MORE_PAGES` | the last page had no next-page token |
   | `EMPTY_PAGE` | a page came back with no reviews |
   | `REPEATED_TOKEN` | SerpApi repeated a token, which would loop forever |
   | `NO_RESULTS` | SerpApi says the place has no reviews |

4. **Clean every page before returning it:**
   - the API key is removed if it appears anywhere;
   - **reviewer identity is scrubbed** with S1-021's `scrub_page`, the same rule used for rejected records: no reviewer names, profile links, avatars, contributor ids, photos or owner replies. Reviewer activity stats (`local_guide`, `reviews`, `photos`) stay;
   - NUL characters and NaN/infinity are removed, so the page fits a `jsonb` column.

   Each review keeps its `review_id`, `rating`, `snippet`, `extracted_snippet` (original and translated text), date and other fields, so normalizing a stored page gives the same reviews as normalizing it straight away.

**No resume.** A failed stage restarts from its beginning (pipeline design), so a retried INGESTION stage fetches from page 1 again. The worker replaces that run's earlier raw pages: `(run_id, page_number)` is unique.

## 4. What a fetch returns (`FetchResult`)

| Field | Content |
|---|---|
| `data_id` | the place id used |
| `pages` | `RawPage(page_number, payload, next_page_token)` per page: one `ingestion_raw_pages` row each |
| `payloads` | the page payloads, for `normalize_pages` |
| `review_records` | every review across the pages, each identified by `review_id` |
| `place_details` | from page 1's `place_info`: `title`, `address`, `category`, `rating`, `review_count` |
| `run_details` | provider, engine, sort, language, `max_reviews`, `pages_planned`, `pages_fetched`, `searches_used`, `reviews_fetched`, `stopped_because` |
| `reviews_fetched`, `searches_used`, `stopped_because` | counts and stop reason |

`pages_planned` vs `pages_fetched` shows what a run is missing, as the `ingestion_runs.source_metadata` migration asks.

## 5. Cost and limits

One request = one SerpApi search, including an empty "no results" answer. Failed and errored searches are not counted ([pricing FAQ](https://serpapi.com/pricing)).

**SerpApi free plan, checked Nov 2, 2026: 250 searches a month, and at most 50 an hour,** per account.

Searches per run = `pages_planned(cap)` = 1 + ⌈(cap − 10) / 20⌉:

| `MAX_REVIEWS_PER_RUN` | Searches per run | Runs per month (one free account) |
|---|---|---|
| 100 | 6 | 41 |
| 200 | 11 | 22 |
| 500 | 26 | 9 |
| 1000 (current default) | 51 | 4 |

At the default of 1000, one run needs more searches than the hourly limit allows, so it fails partway with `QUOTA_EXCEEDED`. A run of 51 slow requests could also outlast the 10-minute stale-run limit (`STALE_RUN_AFTER`): if the owner then queues the same target again, `create_run` marks the slow run FAILED (`TIMEOUT`) and the worker's late `mark_succeeded` is refused and rolled back. Set a lower value in `backend/.env` for development.

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

`backend/tests/unit/test_serpapi.py` never calls SerpApi or Google: every request goes to a fake transport (`httpx.MockTransport`).

- Two **recorded real pages** (Smashburger, Newark, newest first) from the SerpApi spike are the fixtures: `backend/app/fixtures/serpapi_reviews_with_missing_text.json` (also used by S1-021) and `serpapi_reviews_next_page.json`. Reviewer identities were replaced with fakes by the spike; four staff first names inside one review's text are replaced with `[name]`.
- Error responses follow SerpApi's [status and error codes page](https://serpapi.com/api-status-and-error-codes); they were not recorded live.
- A handoff test feeds the fetched pages into S1-021's `normalize_pages`: 18 reviews kept, 12 rating-only rejected, and fetched = kept + rejected + duplicates.
- `backend/tests/unit/test_worker.py` runs the worker with the real adapter and in-memory repositories: the order of steps, commits and rollbacks, retries, and every failure code.
- `backend/tests/db/test_ingestion_worker.py` runs it against Postgres (set `TEST_DATABASE_URL`, as for the other database tests), with only SerpApi and S1-022's review store faked.

## 9. Not covered yet

- `?cid=` and search links (they need a paid lookup to find the place).
- Retries inside a fetch after a timeout or server error (the whole stage is retried instead, when the user retries the run).
- Failing runs left RUNNING by a crashed worker: they are failed as stale only when the owner next queues that target (`create_run`).
- Deploying the worker (section 2).
- Google's cookie-consent redirect, which servers in the EU can get when following short links.
- The spike's plan of several sort orders per run (lowest-rated, newest, most relevant).
- Not yet confirmed against live SerpApi: short-link redirects, the last page having no next-page token, and `review_id` staying the same across runs.
