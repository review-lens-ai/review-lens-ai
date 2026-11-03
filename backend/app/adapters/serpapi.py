"""S1-020: fetch a target's Google Maps reviews through SerpApi.

Rules: S1-BR-019, S1-BR-020, S1-BR-021, S1-BR-024.
Approach and costs: docs/architecture/review-ingestion.md.

How the worker (app/worker.py) uses this module, in the INGESTION stage:

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

Rules this module enforces:

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

import json
import logging
import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Self
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from app.repositories.ingestion_runs import ErrorCode
from app.services.normalize_reviews import scrub_page

logger = logging.getLogger(__name__)

# httpx logs every request URL at INFO level, and SerpApi takes the API key as a
# URL parameter. Raising httpx's log level keeps the key out of the logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

PROVIDER = "serpapi"
SERPAPI_URL = "https://serpapi.com/search"
ENGINE = "google_maps_reviews"
SORT_ORDERS = ("qualityScore", "newestFirst", "ratingHigh", "ratingLow")
DEFAULT_SORT = "newestFirst"  # most recent first: what is happening now
LANGUAGE = "en"
FIRST_PAGE_SIZE = 10  # SerpApi always returns 10 reviews on the first page
PAGE_SIZE = 20  # SerpApi's maximum `num` on later pages
REQUEST_TIMEOUT_SECONDS = 30
MAX_REDIRECTS = 5
# SerpApi answers HTTP 200 with this error when a place has no reviews.
NO_RESULTS_ERROR = "Google hasn't returned any results"

# A Google Maps place id as SerpApi expects it,
# e.g. 0x89c2537da9b3163d:0x2be30fcff506fec8
_DATA_ID = re.compile(r"0x[0-9a-fA-F]+:0x[0-9a-fA-F]+")
# Full place links carry it after "!1s" in their data= part.
_DATA_ID_IN_LINK = re.compile(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)")
_GOOGLE_HOST = re.compile(
    r"^(www\.|maps\.)?google\.(com|co\.[a-z]{2}|com\.[a-z]{2}|[a-z]{2})$"
)
_SHORT_LINK_HOSTS = frozenset({"maps.app.goo.gl", "goo.gl"})
# SerpApi's place_info field -> our analysis_targets.source_metadata key.
_PLACE_FIELDS = {
    "title": "title",
    "address": "address",
    "type": "category",
    "rating": "rating",
    "reviews": "review_count",
}


# --- Failures ------------------------------------------------------------------


class ReviewFetchError(Exception):
    """Base class for every failure raised here.

    The message is technical (for server logs) and never contains the API key.
    `error_code` is the ErrorCode to store on the run; `pages` holds whatever
    was fetched before the failure, already scrubbed, so it can still be saved.
    """

    error_code = ErrorCode.UNEXPECTED

    def __init__(self, message: str, pages: list["RawPage"] | None = None):
        super().__init__(message)
        self.pages: list[RawPage] = pages or []


class UnsupportedSourceUrlError(ReviewFetchError):
    """The link does not lead to a Google Maps place we can identify."""

    error_code = ErrorCode.SOURCE_UNREACHABLE


class ReviewFetchTimeoutError(ReviewFetchError):
    """SerpApi (or Google, for a short link) did not answer in time."""

    error_code = ErrorCode.TIMEOUT


class ReviewQuotaExceededError(ReviewFetchError):
    """SerpApi refused the request: monthly searches used up, or hourly limit hit."""

    error_code = ErrorCode.QUOTA_EXCEEDED


class ReviewProviderError(ReviewFetchError):
    """Any other SerpApi failure: server error, bad response, network failure."""

    error_code = ErrorCode.SOURCE_UNREACHABLE


class ReviewProviderConfigError(ReviewProviderError):
    """The API key is missing, or SerpApi rejected it. A setup problem on our side."""

    error_code = ErrorCode.UNEXPECTED


# --- Finding the place --------------------------------------------------------


def extract_data_id(url: str) -> str | None:
    """The SerpApi place id written inside a Google Maps link, or None.

    Works for full place links (".../data=...!1s0x...:0x...") and links with an
    ftid parameter. Short share links have to be followed first (resolve_data_id).
    """
    match = _DATA_ID_IN_LINK.search(unquote(url))
    if match:
        return match.group(1)
    for value in parse_qs(urlsplit(url).query).get("ftid", []):
        if _DATA_ID.fullmatch(value):
            return value
    return None


def resolve_data_id(url: str, client: httpx.Client) -> str:
    """The SerpApi place id for a Google Maps link.

    Short share links (maps.app.goo.gl) are followed one redirect at a time, and
    only to Google hosts, so a link can never make the server call an arbitrary
    address. Links without a place id (searches, ?cid= links) are unsupported.
    """
    data_id = extract_data_id(url)
    if data_id:
        return data_id

    try:
        current = httpx.URL(url)
    except httpx.InvalidURL:
        raise UnsupportedSourceUrlError("The source link is not a valid URL.") from None
    if current.host not in _SHORT_LINK_HOSTS:
        raise UnsupportedSourceUrlError("The source link does not identify a place.")

    for _ in range(MAX_REDIRECTS):
        try:
            response = client.get(current, follow_redirects=False)
        except httpx.TimeoutException:
            raise ReviewFetchTimeoutError(
                "The short link did not answer in time."
            ) from None
        except httpx.HTTPError as error:
            raise UnsupportedSourceUrlError(
                f"Could not open the short link ({type(error).__name__})."
            ) from None
        location = response.headers.get("location")
        if not response.is_redirect or not location:
            break
        current = current.join(location)
        if current.scheme != "https" or not _is_google_host(current.host):
            raise UnsupportedSourceUrlError("The short link leads outside Google Maps.")
        data_id = extract_data_id(str(current))
        if data_id:
            return data_id

    raise UnsupportedSourceUrlError("Could not find the place behind the short link.")


def _is_google_host(host: str) -> bool:
    return host in _SHORT_LINK_HOSTS or bool(_GOOGLE_HOST.match(host))


# --- What a fetch returns -----------------------------------------------------


class StopReason(StrEnum):
    """Why a fetch stopped asking for more pages (recorded in run_details)."""

    CAP_REACHED = "CAP_REACHED"  # fetched max_reviews reviews
    NO_MORE_PAGES = "NO_MORE_PAGES"  # the last page had no next-page token
    EMPTY_PAGE = "EMPTY_PAGE"  # a page came back with no reviews
    REPEATED_TOKEN = "REPEATED_TOKEN"  # the provider repeated a token (would loop)
    NO_RESULTS = "NO_RESULTS"  # SerpApi: the place has no reviews


@dataclass(frozen=True)
class RawPage:
    """One provider page, shaped like a row of ingestion_raw_pages."""

    page_number: int  # 1 for the first page of the run, 2 for the next, ...
    payload: dict  # the SerpApi response, API key removed and identity scrubbed
    next_page_token: str | None  # the token the provider gave for the next page


@dataclass
class FetchResult:
    data_id: str
    sort_by: str
    max_reviews: int
    pages: list[RawPage]
    reviews_fetched: int
    searches_used: int  # SerpApi requests that cost a search
    stopped_because: StopReason

    @property
    def payloads(self) -> list[dict]:
        """The page payloads, in order: what normalize_pages (S1-021) takes."""
        return [page.payload for page in self.pages]

    @property
    def review_records(self) -> list[dict]:
        """Every individual review across the pages, in order, as scrubbed.

        Each record is identified by its "review_id" and carries the review's
        "rating" and text ("snippet", plus "extracted_snippet.original" when
        Google translated it). Turning records into saved reviews, and rejecting
        unusable ones, is normalization (S1-021).
        """
        return [
            record
            for page in self.pages
            for record in page.payload.get("reviews", [])
            if isinstance(record, dict)
        ]

    @property
    def place_details(self) -> dict | None:
        """What SerpApi says about the place (page 1 only), for
        analysis_targets.source_metadata: title, address, category, rating and
        review count. None if the first page has no place_info."""
        info = self.pages[0].payload.get("place_info") if self.pages else None
        if not isinstance(info, dict):
            return None
        return {
            ours: info[theirs]
            for theirs, ours in _PLACE_FIELDS.items()
            if theirs in info
        }

    @property
    def run_details(self) -> dict:
        """What the run asked for and what happened, for
        ingestion_runs.source_metadata. Pages planned vs fetched shows what a
        run is missing."""
        return {
            "provider": PROVIDER,
            "engine": ENGINE,
            "sort_by": self.sort_by,
            "language": LANGUAGE,
            "max_reviews": self.max_reviews,
            "pages_planned": pages_planned(self.max_reviews),
            "pages_fetched": len(self.pages),
            "searches_used": self.searches_used,
            "reviews_fetched": self.reviews_fetched,
            "stopped_because": self.stopped_because.value,
        }


def pages_planned(max_reviews: int) -> int:
    """Pages (= SerpApi searches) needed to reach max_reviews: 10 on the first
    page, 20 on each later one."""
    if max_reviews <= FIRST_PAGE_SIZE:
        return 1
    return 1 + math.ceil((max_reviews - FIRST_PAGE_SIZE) / PAGE_SIZE)


# --- Fetching reviews ---------------------------------------------------------


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
    ) -> FetchResult:
        """Fetch review pages for the place behind source_url, up to max_reviews.

        data_id skips looking up the place: pass analysis_targets.source_place_id
        when it is known. Each request costs one SerpApi search: 10 reviews on the
        first page, up to 20 on each later page (see pages_planned).
        """
        if max_reviews < 1:
            raise ValueError("max_reviews must be at least 1.")
        if sort_by not in SORT_ORDERS:
            raise ValueError(f"sort_by must be one of {', '.join(SORT_ORDERS)}.")
        data_id = data_id or self.resolve_place(source_url)

        pages: list[RawPage] = []
        fetched = 0
        searches = 0
        token: str | None = None
        stopped = StopReason.CAP_REACHED
        while fetched < max_reviews:
            params = {
                "engine": ENGINE,
                "data_id": data_id,
                "sort_by": sort_by,
                "hl": LANGUAGE,
            }
            if token:
                # SerpApi refuses `num` on the first page.
                params["next_page_token"] = token
                params["num"] = min(PAGE_SIZE, max_reviews - fetched)
            try:
                payload = self._get_page(params)
            except ReviewFetchError as error:
                error.pages = pages
                raise
            searches += 1

            if payload is None:
                stopped = StopReason.NO_RESULTS
                break
            reviews = payload.get("reviews")
            if not isinstance(reviews, list) or not reviews:
                stopped = StopReason.EMPTY_PAGE
                break

            next_token = _next_page_token(payload)
            pages.append(RawPage(len(pages) + 1, payload, next_token))
            fetched += len(reviews)

            if not next_token:
                stopped = StopReason.NO_MORE_PAGES
                break
            if next_token == token:
                stopped = StopReason.REPEATED_TOKEN
                break
            token = next_token

        return FetchResult(
            data_id, sort_by, max_reviews, pages, fetched, searches, stopped
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


def _next_page_token(page: dict) -> str | None:
    pagination = page.get("serpapi_pagination")
    if isinstance(pagination, dict):
        token = pagination.get("next_page_token")
        if isinstance(token, str) and token:
            return token
    return None
