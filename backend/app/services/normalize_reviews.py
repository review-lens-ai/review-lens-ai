"""S1-021: turn raw review records from the review source into ReviewCreate rows.

Rules: S1-BR-020, S1-BR-021, S1-BR-024.

Where this sits in ingestion (S1-019 to S1-022):

    pages = ...                       # S1-020: the provider's pages for one run,
                                      #   as saved in ingestion_raw_pages.payload
    result = normalize_pages(pages)   # S1-021: this module
    ...                               # S1-022: save result.reviews and
                                      #   result.rejected for the run
    runs.mark_succeeded(run_id, len(result.reviews), len(result.rejected))

Rules this module enforces:

- REQUIRED: non-blank review text and a whole-number rating from 1 to 5. A record
  missing either is REJECTED with a fixed reason. One bad record never stops the
  rest of the batch, and nothing here raises because of a bad record.
- ORIGINAL TEXT: when Google translates a review, the reviewer's own words are
  kept (extracted_snippet.original), not the translation. snippet is used only
  when there is no original.
- OPTIONAL: the source review id, reviewer display name, date, link and useful
  extras are kept when present. If one is missing or malformed it becomes None
  (or is left out of source_metadata); the review itself is still kept.
- MINIMAL PEOPLE DATA: the reviewer's display name is kept in author_name, as
  S1-021 asks. Everything else that identifies or shows a person is dropped:
  profile links, avatars, contributor ids, attached images and owner replies.
  Rejected records are stored without the name either, since diagnosing a bad
  record does not need it.
- DUPLICATES: the same review can come back on several pages and sort orders.
  The first copy is kept; later copies are counted in `duplicates`, not
  rejected, because they are not bad data. Normalize all of a run's pages in
  one call so repeats across pages are caught too.
- STORABLE: values bound for jsonb columns (source_metadata, raw_record) have
  NUL characters removed and NaN / infinity replaced with null, because
  Postgres rejects them and one such record would fail the whole save.

Input field names are those of SerpApi's Google Maps Reviews API.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.schemas.reviews import ReviewCreate

MIN_RATING = 1
MAX_RATING = 5

# Reviewer fields that identify a person. Only "name" is ever kept, as
# author_name on a saved review; all of them are removed from rejected records.
_IDENTITY_FIELDS = frozenset({"name", "link", "thumbnail", "contributor_id"})
# Reviewer fields kept as metadata: they describe the reviewer, not who they are.
_REVIEWER_STATS = ("local_guide", "reviews", "photos")
# Top-level fields that can show or name people (photos, owner replies).
_PEOPLE_FIELDS = frozenset({"images", "response"})


class RejectionReason(StrEnum):
    """Why a record was skipped. Stored in ingestion_rejections.reason."""

    NOT_A_RECORD = "not a review record"
    MISSING_TEXT = "missing review text"
    MISSING_RATING = "missing rating"
    INVALID_RATING = "invalid rating"


@dataclass(frozen=True)
class RejectedRecord:
    reason: RejectionReason
    # The source record with people data removed (ingestion_rejections.raw_record).
    raw_record: Any


@dataclass
class NormalizationResult:
    reviews: list[ReviewCreate] = field(default_factory=list)
    rejected: list[RejectedRecord] = field(default_factory=list)
    # Repeats of a review already in `reviews` (same source_review_id).
    duplicates: int = 0


def normalize_pages(pages: Iterable[Any]) -> NormalizationResult:
    """Normalize every review on a run's provider pages, as one batch.

    Each page is a SerpApi response (ingestion_raw_pages.payload) with its
    reviews under "reviews". A page without a reviews list adds nothing.
    """
    return normalize_reviews(record for page in pages for record in _page_reviews(page))


def normalize_reviews(raw_records: Iterable[Any]) -> NormalizationResult:
    """Normalize a batch. Usable reviews, rejections and duplicates are reported
    separately so the run's counts can be shown to the user."""
    result = NormalizationResult()
    seen_ids: set[str] = set()

    for raw in raw_records:
        review, reason = normalize_review(raw)
        if reason is not None:
            result.rejected.append(RejectedRecord(reason, _without_people_data(raw)))
            continue

        source_id = review.source_review_id
        if source_id is not None:
            if source_id in seen_ids:
                result.duplicates += 1
                continue
            seen_ids.add(source_id)
        result.reviews.append(review)

    return result


def normalize_review(raw: Any) -> tuple[ReviewCreate | None, RejectionReason | None]:
    """One record: (review, None) when usable, otherwise (None, reason).

    When a record has several problems, the first one found is reported, in
    this order: not a record, missing text, missing rating, invalid rating.
    """
    if not isinstance(raw, dict):
        return None, RejectionReason.NOT_A_RECORD

    review_text = _review_text(raw)
    if review_text is None:
        return None, RejectionReason.MISSING_TEXT

    if raw.get("rating") is None:
        return None, RejectionReason.MISSING_RATING
    rating = _parse_rating(raw["rating"])
    if rating is None:
        return None, RejectionReason.INVALID_RATING

    review = ReviewCreate(
        rating=rating,
        review_text=review_text,
        author_name=_reviewer_name(raw.get("user")),
        reviewed_at=_parse_datetime(raw.get("iso_date")),
        source_review_id=_clean_text(raw.get("review_id")),
        source_url=_https_url(raw.get("link")),
        source_metadata=_source_metadata(raw),
    )
    return review, None


def _clean_text(value: Any) -> str | None:
    """Trimmed text, or None when absent, not a string, or blank.

    NUL characters are removed because Postgres text columns reject them, and
    one such review would otherwise fail the whole save.
    """
    if not isinstance(value, str):
        return None
    cleaned = value.replace("\x00", "").strip()
    return cleaned or None


def _review_text(raw: dict) -> str | None:
    """The reviewer's own words, or None if the record has no usable text.

    SerpApi puts the review in "snippet". When Google translated it, the
    original is also in extracted_snippet.original (and the translation in
    extracted_snippet.translated). The original wins; snippet is the fallback.
    """
    extracted = raw.get("extracted_snippet")
    if isinstance(extracted, dict):
        original = _clean_text(extracted.get("original"))
        if original is not None:
            return original
    return _clean_text(raw.get("snippet"))


def _parse_rating(value: Any) -> int | None:
    """A whole number from 1 to 5. SerpApi sends floats such as 4.0.

    Rejected: booleans (True is an int in Python), strings, fractions such as
    4.5, NaN, infinity, and anything outside 1 to 5.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    rating = int(value)
    if MIN_RATING <= rating <= MAX_RATING:
        return rating
    return None


def _parse_datetime(value: Any) -> datetime | None:
    """An ISO 8601 timestamp such as "2026-04-02T15:02:46Z", or None.

    A timestamp without a time zone is taken as UTC.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _reviewer_name(user: Any) -> str | None:
    """The reviewer's display name (user.name), or None if absent or blank."""
    if not isinstance(user, dict):
        return None
    return _clean_text(user.get("name"))


def _https_url(value: Any) -> str | None:
    """The link to the original review, only if it is an https URL."""
    if isinstance(value, str) and value.startswith("https://"):
        return value
    return None


def _source_metadata(raw: dict) -> dict[str, Any]:
    """Extras useful for later analysis. Only fields that are present and well
    formed are included, so absent metadata never causes an error."""
    metadata: dict[str, Any] = {}

    edited_at = _parse_datetime(raw.get("iso_date_of_last_edit"))
    if edited_at is not None:
        metadata["edited_at"] = edited_at.isoformat()

    likes = raw.get("likes")
    if isinstance(likes, int) and not isinstance(likes, bool) and likes >= 0:
        metadata["likes"] = likes

    # Per-topic scores such as {"food": 1, "service": 4}. Values can also be text.
    details = raw.get("details")
    if isinstance(details, dict) and details:
        metadata["details"] = _json_safe(details)

    user = raw.get("user")
    if isinstance(user, dict):
        reviewer = {key: user[key] for key in _REVIEWER_STATS if key in user}
        if reviewer:
            metadata["reviewer"] = _json_safe(reviewer)

    return metadata


def _page_reviews(page: Any) -> list:
    """The review records on one provider page, or [] if it has none."""
    if isinstance(page, dict) and isinstance(page.get("reviews"), list):
        return page["reviews"]
    return []


def _without_people_data(raw: Any) -> Any:
    """A copy of a rejected record that is safe to store for diagnosis."""
    if not isinstance(raw, dict):
        return _json_safe(raw)
    cleaned = {key: value for key, value in raw.items() if key not in _PEOPLE_FIELDS}
    user = raw.get("user")
    if isinstance(user, dict):
        cleaned["user"] = {
            key: value for key, value in user.items() if key not in _IDENTITY_FIELDS
        }
    return _json_safe(cleaned)


def _json_safe(value: Any) -> Any:
    """A copy that a Postgres jsonb column accepts: NUL characters removed from
    text, and NaN or infinity (not valid JSON numbers) replaced with None."""
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {
            str(key).replace("\x00", ""): _json_safe(item)
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value
