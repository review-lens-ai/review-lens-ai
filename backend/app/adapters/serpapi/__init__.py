"""S1-020: fetch a target's Google Maps reviews through SerpApi.

Rules: S1-BR-019, S1-BR-020, S1-BR-021, S1-BR-024.
Approach and costs: docs/architecture/review-ingestion.md.

How the worker (app/worker.py) uses this package, in the INGESTION stage:

    with SerpApiReviewSource.from_settings(settings) as source:
        place_id = target["source_place_id"] or source.resolve_place(url)  # free
        # (the worker saves place_id here, before spending any search)
        result = source.fetch_reviews(
            target["source_url"], settings.max_reviews_per_run, data_id=place_id
        )
    # result.data_id        -> analysis_targets.source_place_id
    # result.place_details  -> analysis_targets.source_metadata
    # result.pages          -> ingestion_raw_pages (page_number, payload, next_page_token)
    # result.run_details    -> ingestion_runs.source_metadata
    # result.payloads       -> normalize_pages(...) in the NORMALIZATION stage (S1-021)

Rules this package enforces:

- REAL DATA ONLY: every page comes from SerpApi. Nothing here returns sample or
  made-up reviews; a failure raises instead.
- NO REVIEWER IDENTITY: every page is returned after S1-021's identity scrub
  (scrub_page), so a stored raw page never holds reviewer names, profile links,
  photos or owner replies.
- SERVER-SIDE KEY: the API key comes from REVIEW_PROVIDER_API_KEY. It is never
  logged, never put in an error message, and removed from every returned page.
- CAP: a run stops once it has fetched max_reviews reviews (MAX_REVIEWS_PER_RUN).
- DISTINCT FAILURES: a timeout, a used-up quota and any other provider failure
  raise different exceptions. Each carries the ErrorCode to store on the run,
  and the pages fetched before it.
- NO RESUME: a failed fetch restarts from the first page on retry (the pipeline
  design restarts a failed stage from its beginning).
"""

from app.adapters.serpapi.client import SERPAPI_URL, SerpApiReviewSource
from app.adapters.serpapi.errors import (
    ReviewFetchError,
    ReviewFetchTimeoutError,
    ReviewProviderConfigError,
    ReviewProviderError,
    ReviewQuotaExceededError,
    UnsupportedSourceUrlError,
)
from app.adapters.serpapi.place import extract_data_id, resolve_data_id
from app.adapters.serpapi.results import (
    FetchResult,
    RawPage,
    StopReason,
    pages_planned,
)

__all__ = [
    "SERPAPI_URL",
    "FetchResult",
    "RawPage",
    "ReviewFetchError",
    "ReviewFetchTimeoutError",
    "ReviewProviderConfigError",
    "ReviewProviderError",
    "ReviewQuotaExceededError",
    "SerpApiReviewSource",
    "StopReason",
    "UnsupportedSourceUrlError",
    "extract_data_id",
    "pages_planned",
    "resolve_data_id",
]
