"""What a fetch returns: the pages, and details about the place and the run."""

import math
from dataclasses import dataclass
from enum import StrEnum

PROVIDER = "serpapi"
ENGINE = "google_maps_reviews"
LANGUAGE = "en"
FIRST_PAGE_SIZE = 10  # SerpApi always returns 10 reviews on the first page
PAGE_SIZE = 20  # SerpApi's maximum `num` on later pages

# SerpApi's place_info field -> our analysis_targets.source_metadata key.
_PLACE_FIELDS = {
    "title": "title",
    "address": "address",
    "type": "category",
    "rating": "rating",
    "reviews": "review_count",
}


class StopReason(StrEnum):
    """Why a fetch stopped asking for more pages (recorded in run_details)."""

    CAP_REACHED = "CAP_REACHED"  # fetched max_reviews reviews
    NO_MORE_PAGES = "NO_MORE_PAGES"  # the last page had no next-page token
    EMPTY_PAGE = "EMPTY_PAGE"  # a page came back with no reviews
    REPEATED_TOKEN = "REPEATED_TOKEN"  # the provider repeated a token (would loop)
    NO_RESULTS = "NO_RESULTS"  # SerpApi: the place has no reviews
    # S3-009: a refresh reached reviews the target already has, and every older
    # review was collected before, so there is nothing left to fetch.
    CAUGHT_UP = "CAUGHT_UP"
    SEARCH_LIMIT = "SEARCH_LIMIT"  # used max_searches searches (MAX_SEARCHES_PER_RUN)


@dataclass(frozen=True)
class OlderCursor:
    """S3-009: where the next run continues collecting older reviews
    (analysis_targets.older_page_token and all_reviews_collected)."""

    page_token: str | None  # SerpApi's token for the next older page, if any
    all_collected: bool  # the last page was reached: there are no older reviews


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
    # S3-009 (refresh). Reviews whose review_id the target had not seen before.
    new_reviews: int = 0
    # The fetch reached reviews the target already has, at the top of the list.
    caught_up: bool = False
    # It then jumped to the saved older-review cursor ...
    resumed_older: bool = False
    # ... which SerpApi refused, so it walked down from the top instead.
    resume_rejected: bool = False
    # Where the next run continues, or None to leave the saved cursor as it is.
    older_cursor: OlderCursor | None = None

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
            "new_reviews_fetched": self.new_reviews,
            "stopped_because": self.stopped_because.value,
            "caught_up": self.caught_up,
            "resumed_older": self.resumed_older,
            "resume_rejected": self.resume_rejected,
        }


def pages_planned(max_reviews: int) -> int:
    """Pages (= SerpApi searches) needed to reach max_reviews: 10 on the first
    page, 20 on each later one."""
    if max_reviews <= FIRST_PAGE_SIZE:
        return 1
    return 1 + math.ceil((max_reviews - FIRST_PAGE_SIZE) / PAGE_SIZE)
