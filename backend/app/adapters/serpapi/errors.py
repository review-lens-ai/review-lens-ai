"""Failures raised while finding a place or fetching its reviews."""

from app.adapters.serpapi.results import RawPage
from app.repositories.ingestion_runs import ErrorCode


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
