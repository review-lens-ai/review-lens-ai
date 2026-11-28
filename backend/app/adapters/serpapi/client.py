"""SerpApiReviewSource: the requests to SerpApi, and keeping the API key secret."""

import json
import logging
from collections.abc import Collection
from typing import Any, Self

import httpx

from app.adapters.serpapi.errors import (
    ReviewFetchError,
    ReviewFetchTimeoutError,
    ReviewProviderConfigError,
    ReviewProviderError,
    ReviewQuotaExceededError,
)
from app.adapters.serpapi.place import resolve_data_id
from app.adapters.serpapi.results import (
    ENGINE,
    LANGUAGE,
    PAGE_SIZE,
    FetchResult,
    OlderCursor,
    RawPage,
    StopReason,
)
from app.services.normalize_reviews import scrub_page

logger = logging.getLogger(__name__)

# httpx logs every request URL at INFO level, and SerpApi takes the API key as a
# URL parameter. Raising httpx's log level keeps the key out of the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

SERPAPI_URL = "https://serpapi.com/search"
SORT_ORDERS = ("qualityScore", "newestFirst", "ratingHigh", "ratingLow")
DEFAULT_SORT = "newestFirst"  # most recent first: what is happening now
REQUEST_TIMEOUT_SECONDS = 30
# SerpApi answers HTTP 200 with this error when a place has no reviews.
NO_RESULTS_ERROR = "Google hasn't returned any results"


class SerpApiReviewSource:
    """Fetches review pages for one place from SerpApi's Google Maps Reviews API.

    Pass an httpx.Client to control the connection (tests pass a fake one);
    otherwise one is created with REQUEST_TIMEOUT_SECONDS and closed by close().
    """

    def __init__(self, api_key: str | None, http_client: httpx.Client | None = None):
        if not api_key:
            raise ReviewProviderConfigError("REVIEW_PROVIDER_API_KEY is not set.")
        self._api_key = api_key
        self._owns_client = http_client is None
        self._client = http_client or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    @classmethod
    def from_settings(cls, settings: Any, http_client: httpx.Client | None = None):
        """Build from app.core.config.Settings (review_provider_api_key)."""
        key = settings.review_provider_api_key
        return cls(key.get_secret_value() if key else None, http_client)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(api_key=***)"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def resolve_place(self, source_url: str) -> str:
        """The SerpApi place id for a Google Maps link (see resolve_data_id).
        Costs no SerpApi search, so the worker can save it before fetching."""
        return resolve_data_id(source_url, self._client)

    def fetch_reviews(
        self,
        source_url: str,
        max_reviews: int,
        *,
        data_id: str | None = None,
        sort_by: str = DEFAULT_SORT,
        known_review_ids: Collection[str] = frozenset(),
        older_page_token: str | None = None,
        all_collected: bool = False,
        max_searches: int | None = None,
    ) -> FetchResult:
        """Fetch review pages for the place behind source_url, newest first,
        until max_reviews NEW reviews (review_ids not in known_review_ids).

        data_id skips looking up the place: pass analysis_targets.source_place_id
        when it is known. Each request costs one SerpApi search: 10 reviews on the
        first page, up to 20 on each later page (see pages_planned).

        A first ingestion (known_review_ids empty) reads from the top until the
        cap or the last page.

        A refresh (S3-009, known_review_ids = every review_id the target has seen)
        does two things in one run:
        1. New reviews: read from the top until a page on which every review is
           already known (caught_up). A page that mixes new and known reviews
           does not count: that one extra page is a margin for reviews Google
           publishes late or out of date order.
        2. Older reviews: jump to older_page_token, where the deepest earlier run
           stopped, and keep reading until the cap. If SerpApi refuses that token,
           or there is none, walk on down from the top instead (resume_rejected).
           If all_collected, there is nothing older and the run ends (CAUGHT_UP).

        max_searches caps the searches of one run, whatever it finds. The result's
        older_cursor says where the next run should continue.
        """
        if max_reviews < 1:
            raise ValueError("max_reviews must be at least 1.")
        if sort_by not in SORT_ORDERS:
            raise ValueError(f"sort_by must be one of {', '.join(SORT_ORDERS)}.")
        data_id = data_id or self.resolve_place(source_url)

        seen = set(known_review_ids)
        refresh = bool(seen)
        pages: list[RawPage] = []
        fetched = new = searches = 0
        token: str | None = None
        stopped: StopReason | None = None
        caught_up = resumed = resume_rejected = False
        older_pages = 0  # pages read past the reviews the target already had
        walk_token: str | None = None  # where to walk on from if the jump fails
        used_tokens: set[str] = set()  # a token seen twice would loop for ever
        resuming = False  # the next request is the jump to older_page_token

        while stopped is None:
            if new >= max_reviews:
                stopped = StopReason.CAP_REACHED
                break
            if max_searches is not None and searches >= max_searches:
                stopped = StopReason.SEARCH_LIMIT
                break
            params = {
                "engine": ENGINE,
                "data_id": data_id,
                "sort_by": sort_by,
                "hl": LANGUAGE,
            }
            if token:
                # SerpApi refuses `num` on the first page.
                params["next_page_token"] = token
                params["num"] = min(PAGE_SIZE, max_reviews - new)
            try:
                payload = self._get_page(params)
            except ReviewFetchError as error:
                if resuming and type(error) is ReviewProviderError:
                    # Most likely an expired or unknown token: walk down instead.
                    logger.info("SerpApi refused the saved older-review token")
                    resuming, resumed, resume_rejected = False, False, True
                    token = walk_token
                    if token is None:
                        stopped = StopReason.NO_MORE_PAGES
                    continue
                error.pages = pages
                raise
            searches += 1

            reviews = payload.get("reviews") if payload is not None else None
            if not isinstance(reviews, list) or not reviews:
                if resuming:
                    # An empty answer to the jump says nothing about the place:
                    # treat it like a refused token and walk down instead.
                    resuming, resumed, resume_rejected = False, False, True
                    token = walk_token
                    if token is None:
                        stopped = StopReason.NO_MORE_PAGES
                    continue
                stopped = (
                    StopReason.NO_RESULTS if payload is None else StopReason.EMPTY_PAGE
                )
                break

            next_token = _next_page_token(payload)
            ids = [_review_id(review) for review in reviews]
            page_new = sum(1 for i in ids if i is None or i not in seen)
            seen.update(i for i in ids if i is not None)
            pages.append(RawPage(len(pages) + 1, payload, next_token))
            fetched += len(reviews)
            new += page_new
            if caught_up:
                older_pages += 1
            resuming = False
            if token:
                used_tokens.add(token)

            if refresh and not caught_up and page_new == 0:
                caught_up = True
                if all_collected:
                    stopped = StopReason.CAUGHT_UP
                    break
                walk_token = next_token
                if older_page_token:
                    token, resumed, resuming = older_page_token, True, True
                    continue
                # No saved cursor: walk on down from here.
            if not next_token:
                stopped = StopReason.NO_MORE_PAGES
                break
            if next_token in used_tokens:
                stopped = StopReason.REPEATED_TOKEN
                break
            token = next_token

        return FetchResult(
            data_id,
            sort_by,
            max_reviews,
            pages,
            fetched,
            searches,
            stopped,
            new_reviews=new,
            caught_up=caught_up,
            resumed_older=resumed,
            resume_rejected=resume_rejected,
            older_cursor=_older_cursor(stopped, pages, refresh, older_pages),
        )

    def _get_page(self, params: dict) -> dict | None:
        """One SerpApi request. Returns the page (key removed, identity scrubbed),
        or None if the place has no reviews."""
        try:
            response = self._client.get(
                SERPAPI_URL, params={**params, "api_key": self._api_key}
            )
        except httpx.TimeoutException:
            # "from None": httpx errors include the request URL, which holds the key.
            raise ReviewFetchTimeoutError("SerpApi did not answer in time.") from None
        except httpx.HTTPError as error:
            raise ReviewProviderError(
                f"Could not reach SerpApi ({type(error).__name__})."
            ) from None

        body = _json_object(response)
        message = self._safe(body.get("error")) if body else None
        status = response.status_code

        if status == 429:
            raise ReviewQuotaExceededError(f"SerpApi refused the request: {message}")
        if status in (401, 403):
            raise ReviewProviderConfigError(
                f"SerpApi rejected the API key (HTTP {status}): {message}"
            )
        if status != 200 or body is None:
            raise ReviewProviderError(f"SerpApi returned HTTP {status}: {message}")
        if message:
            if message.startswith(NO_RESULTS_ERROR):
                return None
            raise ReviewProviderError(f"SerpApi error: {message}")
        if body.get("search_metadata", {}).get("status") == "Error":
            raise ReviewProviderError("SerpApi reported the search as failed.")
        return scrub_page(self._without_api_key(body))

    def _safe(self, text: Any) -> str | None:
        """Provider text that is safe to log: no API key, at most 300 characters."""
        if text is None:
            return None
        return str(text).replace(self._api_key, "[redacted]")[:300]

    def _without_api_key(self, page: dict) -> dict:
        """A copy of the page with the API key removed wherever it appears."""
        text = json.dumps(page, ensure_ascii=False)
        if self._api_key not in text:
            return page
        return json.loads(text.replace(self._api_key, "REDACTED"))


def _json_object(response: httpx.Response) -> dict | None:
    try:
        body = response.json()
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


_END_OF_REVIEWS = (
    StopReason.NO_MORE_PAGES,
    StopReason.EMPTY_PAGE,
    StopReason.NO_RESULTS,
)


def _review_id(review: Any) -> str | None:
    """A review's SerpApi review_id, or None. A review without one always counts
    as new, so it can never end a refresh early."""
    if isinstance(review, dict):
        value = review.get("review_id")
        if isinstance(value, str) and value:
            return value
    return None


def _older_cursor(
    stopped: StopReason, pages: list[RawPage], refresh: bool, older_pages: int
) -> OlderCursor | None:
    """Where the next run continues collecting older reviews; None to keep the
    saved cursor. It only moves when this run read older reviews: every page of a
    first ingestion, or the pages a refresh read past what the target had."""
    if stopped in _END_OF_REVIEWS:
        return OlderCursor(page_token=None, all_collected=True)
    if refresh and older_pages == 0:
        return None
    if stopped in (StopReason.CAP_REACHED, StopReason.SEARCH_LIMIT) and pages:
        return OlderCursor(page_token=pages[-1].next_page_token, all_collected=False)
    return OlderCursor(page_token=None, all_collected=False)


def _next_page_token(page: dict) -> str | None:
    pagination = page.get("serpapi_pagination")
    if isinstance(pagination, dict):
        token = pagination.get("next_page_token")
        if isinstance(token, str) and token:
            return token
    return None
