"""S1-021: normalizing raw review records into ReviewCreate rows."""

import json
import math
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.services.normalize_reviews import (
    RejectionReason,
    normalize_pages,
    normalize_review,
    normalize_reviews,
)

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "fixtures"
FIXTURE = FIXTURES / "serpapi_reviews_with_missing_text.json"

_MISSING = object()


def raw_review(**overrides):
    """One SerpApi review record. Pass field=_MISSING to leave a field out."""
    record = {
        "position": 1,
        "link": "https://www.google.com/maps/reviews/data=example",
        "rating": 4.0,
        "date": "a month ago",
        "iso_date": "2026-09-21T19:42:12Z",
        "iso_date_of_last_edit": "2026-09-22T08:00:00Z",
        "source": "Google",
        "review_id": "review-1",
        "user": {
            "name": "Reviewer 01",
            "link": "https://example.com/contrib/01",
            "contributor_id": "contributor_01",
            "thumbnail": "https://example.com/avatar/01.jpg",
            "local_guide": True,
            "reviews": 23,
            "photos": 7,
        },
        "snippet": "Burgers were great, but the line took 40 minutes.",
        "details": {"food": 5, "service": 2},
        "likes": 3,
        "images": ["https://example.com/photo.jpg"],
        "response": {"snippet": "Thanks Reviewer 01!"},
    }
    for key, value in overrides.items():
        if value is _MISSING:
            record.pop(key, None)
        else:
            record[key] = value
    return record


# --- Valid review -------------------------------------------------------------


def test_valid_review_is_normalized_with_its_metadata():
    review, reason = normalize_review(raw_review())

    assert reason is None
    assert review.rating == 4
    assert review.review_text == "Burgers were great, but the line took 40 minutes."
    assert review.author_name == "Reviewer 01"
    assert review.reviewed_at == datetime(2026, 9, 21, 19, 42, 12, tzinfo=UTC)
    assert review.source_review_id == "review-1"
    assert review.source_url == "https://www.google.com/maps/reviews/data=example"
    assert review.source_metadata == {
        "edited_at": "2026-09-22T08:00:00+00:00",
        "likes": 3,
        "details": {"food": 5, "service": 2},
        "reviewer": {"local_guide": True, "reviews": 23, "photos": 7},
    }


def test_review_text_is_trimmed():
    review, _ = normalize_review(raw_review(snippet="  Good fries.\n"))
    assert review.review_text == "Good fries."


@pytest.mark.parametrize("rating", [1, 5, 1.0, 5.0])
def test_whole_number_ratings_from_1_to_5_are_accepted(rating):
    review, reason = normalize_review(raw_review(rating=rating))
    assert reason is None
    assert review.rating == int(rating)
    assert isinstance(review.rating, int)


# --- Missing required review text ---------------------------------------------


@pytest.mark.parametrize("snippet", [_MISSING, None, "", "   \n\t", 42, ["text"]])
def test_missing_or_unusable_review_text_is_rejected(snippet):
    review, reason = normalize_review(raw_review(snippet=snippet))
    assert review is None
    assert reason == RejectionReason.MISSING_TEXT


# --- Missing or invalid rating ------------------------------------------------


@pytest.mark.parametrize("rating", [_MISSING, None])
def test_missing_rating_is_rejected(rating):
    review, reason = normalize_review(raw_review(rating=rating))
    assert review is None
    assert reason == RejectionReason.MISSING_RATING


@pytest.mark.parametrize(
    "rating",
    [0, 6, -1, 4.5, 0.0, 5.01, "4", "four", True, False, math.nan, math.inf, [4]],
)
def test_invalid_rating_is_rejected(rating):
    review, reason = normalize_review(raw_review(rating=rating))
    assert review is None
    assert reason == RejectionReason.INVALID_RATING


# --- Optional metadata that is absent -----------------------------------------


def test_review_with_only_text_and_rating_is_kept():
    review, reason = normalize_review({"snippet": "Fine.", "rating": 3})

    assert reason is None
    assert review.rating == 3
    assert review.review_text == "Fine."
    assert review.author_name is None
    assert review.reviewed_at is None
    assert review.source_review_id is None
    assert review.source_url is None
    assert review.source_metadata == {}


def test_malformed_optional_fields_are_dropped_not_rejected():
    review, reason = normalize_review(
        raw_review(
            iso_date="last Tuesday",
            iso_date_of_last_edit=12345,
            review_id="   ",
            link="http://insecure.example/review",
            likes=-2,
            details="food was cold",
            user="not an object",
        )
    )

    assert reason is None
    assert review.author_name is None
    assert review.reviewed_at is None
    assert review.source_review_id is None
    assert review.source_url is None
    assert review.source_metadata == {}


@pytest.mark.parametrize("user", [_MISSING, {}, {"name": "   "}, {"name": 5}])
def test_missing_or_blank_reviewer_name_is_left_empty(user):
    review, reason = normalize_review(raw_review(user=user))
    assert reason is None
    assert review.author_name is None


def test_timestamp_without_time_zone_is_treated_as_utc():
    review, _ = normalize_review(raw_review(iso_date="2026-09-21T19:42:12"))
    assert review.reviewed_at == datetime(2026, 9, 21, 19, 42, 12, tzinfo=UTC)


# --- People data --------------------------------------------------------------


def test_only_the_reviewer_display_name_is_kept():
    review, _ = normalize_review(raw_review())
    stored = review.model_dump_json()

    assert review.author_name == "Reviewer 01"
    # Profile link, contributor id, avatar, attached photo and owner reply.
    for leaked in [
        "contrib/01",
        "contributor_01",
        "avatar",
        "photo.jpg",
        "Thanks",
    ]:
        assert leaked not in stored


def test_rejected_records_are_stored_without_people_data():
    result = normalize_reviews([raw_review(snippet=_MISSING)])

    [rejected] = result.rejected
    assert rejected.reason == RejectionReason.MISSING_TEXT
    assert "images" not in rejected.raw_record
    assert "response" not in rejected.raw_record
    assert rejected.raw_record["user"] == {
        "local_guide": True,
        "reviews": 23,
        "photos": 7,
    }
    assert rejected.raw_record["review_id"] == "review-1"  # still diagnosable


# --- Batches ------------------------------------------------------------------


def test_one_bad_record_does_not_stop_the_batch():
    result = normalize_reviews(
        [
            raw_review(review_id="a"),
            "not a record",
            None,
            raw_review(review_id="b", rating=9),
            raw_review(review_id="c"),
        ]
    )

    assert [r.source_review_id for r in result.reviews] == ["a", "c"]
    assert [r.reason for r in result.rejected] == [
        RejectionReason.NOT_A_RECORD,
        RejectionReason.NOT_A_RECORD,
        RejectionReason.INVALID_RATING,
    ]
    assert result.duplicates == 0


def test_duplicate_review_keeps_the_first_copy_and_is_not_a_rejection():
    first = raw_review(review_id="same", snippet="First copy.")
    second = raw_review(review_id="same", snippet="Second copy.")

    result = normalize_reviews([first, second])

    assert [r.review_text for r in result.reviews] == ["First copy."]
    assert result.rejected == []
    assert result.duplicates == 1


def test_reviews_without_a_source_id_are_never_treated_as_duplicates():
    record = raw_review(review_id=_MISSING)
    result = normalize_reviews([record, record])

    assert len(result.reviews) == 2
    assert result.duplicates == 0


def test_nul_characters_are_removed_so_the_review_can_be_saved():
    review, _ = normalize_review(raw_review(snippet="Good\x00 food"))
    assert review.review_text == "Good food"


def test_values_postgres_jsonb_rejects_are_cleaned():
    kept, _ = normalize_review(raw_review(details={"food": "cold\x00", "x": math.nan}))
    assert kept.source_metadata["details"] == {"food": "cold", "x": None}

    result = normalize_reviews([raw_review(rating=math.inf, snippet="bad\x00text")])
    [rejected] = result.rejected
    assert rejected.raw_record["rating"] is None
    assert rejected.raw_record["snippet"] == "badtext"
    # What the save will send to Postgres: valid JSON with no NUL escapes.
    stored = json.dumps(rejected.raw_record, allow_nan=False)
    assert "\\u0000" not in stored


def test_pages_are_normalized_as_one_batch():
    page_1 = {"reviews": [raw_review(review_id="a"), raw_review(review_id="b")]}
    page_2 = {"reviews": [raw_review(review_id="b"), raw_review(review_id="c")]}
    no_reviews = {"serpapi_pagination": {}}

    result = normalize_pages([page_1, no_reviews, page_2, "not a page"])

    assert [r.source_review_id for r in result.reviews] == ["a", "b", "c"]
    assert result.duplicates == 1
    assert result.rejected == []


def test_empty_batch_returns_an_empty_result():
    result = normalize_reviews([])
    assert result.reviews == []
    assert result.rejected == []
    assert result.duplicates == 0


# --- Recorded SerpApi page ----------------------------------------------------


def test_recorded_serpapi_page_is_normalized():
    page = json.loads(FIXTURE.read_text(encoding="utf-8"))

    result = normalize_pages([page])

    # The recorded page has 10 reviews; 5 are star ratings with no written text.
    assert len(result.reviews) == 5
    assert len(result.rejected) == 5
    assert {r.reason for r in result.rejected} == {RejectionReason.MISSING_TEXT}
    assert all(1 <= r.rating <= 5 for r in result.reviews)
    assert all(r.review_text for r in result.reviews)
    assert all(r.reviewed_at is not None for r in result.reviews)
    assert all(r.source_review_id for r in result.reviews)
    # The fixture's names were replaced with fakes before it was committed.
    assert all(r.author_name.startswith("Reviewer ") for r in result.reviews)
    saved = json.dumps([r.model_dump(mode="json") for r in result.reviews])
    assert "example.com/contrib" not in saved
    assert "example.com/avatar" not in saved
