"""S1-020: fetching review pages, and when a run stops."""

import json

import pytest

from app.adapters.serpapi import (
    SERPAPI_URL,
    RawPage,
    StopReason,
    pages_planned,
)
from app.services.normalize_reviews import (
    MAPPING_VERSION,
    RejectionReason,
    normalize_pages,
    scrub_page,
)
from tests.unit.serpapi.conftest import (
    API_KEY,
    DATA_ID,
    NO_RESULTS,
    PAGE_1,
    PAGE_2,
    PLACE_URL,
    TOKEN_AFTER_PAGE_1,
    TOKEN_AFTER_PAGE_2,
    FakeSerpApi,
    reply,
    source_with,
)


def test_recorded_pages_are_fetched_in_order_with_the_key_server_side():
    fake = FakeSerpApi()

    result = source_with(fake).fetch_reviews(PLACE_URL, max_reviews=30)

    assert result.data_id == DATA_ID
    assert result.payloads == [scrub_page(PAGE_1), scrub_page(PAGE_2)]
    assert result.reviews_fetched == 30
    assert result.searches_used == 2
    assert result.stopped_because == StopReason.CAP_REACHED
    first, second = (r.url.params for r in fake.requests)
    assert str(fake.requests[0].url).startswith(SERPAPI_URL)
    assert first["engine"] == "google_maps_reviews"
    assert first["data_id"] == DATA_ID
    assert first["sort_by"] == "newestFirst"
    assert first["hl"] == "en"
    assert first["api_key"] == API_KEY
    assert "num" not in first  # SerpApi refuses num on the first page
    assert second["next_page_token"] == TOKEN_AFTER_PAGE_1
    assert second["num"] == "20"


def test_pages_are_shaped_like_ingestion_raw_pages_rows():
    result = source_with(FakeSerpApi()).fetch_reviews(PLACE_URL, max_reviews=30)

    assert result.pages == [
        RawPage(1, scrub_page(PAGE_1), TOKEN_AFTER_PAGE_1),
        RawPage(2, scrub_page(PAGE_2), TOKEN_AFTER_PAGE_2),
    ]


def test_returned_pages_hold_no_reviewer_identity():
    result = source_with(FakeSerpApi()).fetch_reviews(PLACE_URL, max_reviews=30)

    stored = json.dumps(result.payloads)
    # The fixtures' (fake) reviewer names, profile links, avatars, contributor ids.
    for identity in ["Reviewer ", "example.com/contrib", "example.com/avatar"]:
        assert identity not in stored
    assert "contributor_" not in stored
    for record in result.review_records:
        assert "images" not in record and "response" not in record
        assert set(record.get("user", {})) <= {"local_guide", "reviews", "photos"}


def test_individual_review_records_are_identified_with_text_and_rating():
    result = source_with(FakeSerpApi()).fetch_reviews(PLACE_URL, max_reviews=30)

    records = result.review_records
    assert len(records) == result.reviews_fetched == 30
    ids = [record["review_id"] for record in records]
    assert all(ids) and len(set(ids)) == 30  # each record has its own id
    assert all(1 <= record["rating"] <= 5 for record in records)
    # Written text: 18 of these 30 real reviews have it; 12 are rating-only.
    assert sum(bool(record.get("snippet")) for record in records) == 18
    assert records[0]["review_id"] == PAGE_1["reviews"][0]["review_id"]  # in order
    assert records[10]["review_id"] == PAGE_2["reviews"][0]["review_id"]


def test_fetched_pages_normalize_as_s1_021_expects():
    """The handoff: S1-020's payloads go straight into S1-021's normalize_pages."""
    result = source_with(FakeSerpApi()).fetch_reviews(PLACE_URL, max_reviews=30)

    normalized = normalize_pages(result.payloads)

    assert len(normalized.reviews) == 18
    assert len(normalized.rejected) == 12
    assert {r.reason for r in normalized.rejected} == {RejectionReason.MISSING_TEXT}
    assert normalized.duplicates == 0
    assert all(r.mapping_version == MAPPING_VERSION for r in normalized.reviews)
    # fetched = ingested + rejected + duplicate (ingestion_runs migration)
    total = len(normalized.reviews) + len(normalized.rejected) + normalized.duplicates
    assert total == result.reviews_fetched


def test_place_details_come_from_the_first_page():
    result = source_with(FakeSerpApi()).fetch_reviews(PLACE_URL, max_reviews=30)

    assert result.place_details == {
        "title": "Smashburger",
        "address": "156 Warren St, Newark, NJ 07103",
        "category": "Hamburger restaurant",
        "rating": 3.9,
        "review_count": 338,
    }


def test_run_details_record_what_was_asked_and_what_happened():
    result = source_with(FakeSerpApi()).fetch_reviews(PLACE_URL, max_reviews=50)

    assert result.run_details == {
        "provider": "serpapi",
        "engine": "google_maps_reviews",
        "sort_by": "newestFirst",
        "language": "en",
        "max_reviews": 50,
        "pages_planned": 3,
        "pages_fetched": 2,
        "searches_used": 3,  # the third request returned an empty page
        "reviews_fetched": 30,
        "stopped_because": "EMPTY_PAGE",
    }


@pytest.mark.parametrize(
    ("max_reviews", "pages"), [(1, 1), (10, 1), (11, 2), (30, 2), (200, 11), (1000, 51)]
)
def test_pages_planned_follows_serpapi_page_sizes(max_reviews, pages):
    assert pages_planned(max_reviews) == pages


def test_known_place_id_skips_the_lookup():
    fake = FakeSerpApi()
    result = source_with(fake).fetch_reviews(
        "https://maps.app.goo.gl/never-opened", 10, data_id=DATA_ID
    )
    assert result.data_id == DATA_ID
    assert [r.url.host for r in fake.requests] == ["serpapi.com"]


@pytest.mark.parametrize(
    ("max_reviews", "requests", "last_num"),
    [(1, 1, None), (10, 1, None), (15, 2, "5"), (30, 2, "20")],
)
def test_run_stops_at_the_review_cap(max_reviews, requests, last_num):
    fake = FakeSerpApi()

    result = source_with(fake).fetch_reviews(PLACE_URL, max_reviews)

    assert len(fake.requests) == requests
    assert fake.requests[-1].url.params.get("num") == last_num
    assert result.stopped_because == StopReason.CAP_REACHED


def test_run_stops_when_there_are_no_more_pages():
    last_page = {**PAGE_2, "serpapi_pagination": {}}
    fake = FakeSerpApi(page_2=last_page)

    result = source_with(fake).fetch_reviews(PLACE_URL, 1000)

    assert len(fake.requests) == 2
    assert result.reviews_fetched == 30
    assert result.stopped_because == StopReason.NO_MORE_PAGES
    assert result.pages[-1].next_page_token is None


def test_run_stops_when_a_page_has_no_reviews():
    fake = FakeSerpApi(page_2={**PAGE_2, "reviews": []})

    result = source_with(fake).fetch_reviews(PLACE_URL, 1000)

    assert result.payloads == [scrub_page(PAGE_1)]
    assert result.stopped_because == StopReason.EMPTY_PAGE


def test_run_stops_when_the_next_token_repeats():
    stuck = {
        **PAGE_2,
        "serpapi_pagination": {"next_page_token": TOKEN_AFTER_PAGE_1},
    }
    fake = FakeSerpApi(page_2=stuck)

    result = source_with(fake).fetch_reviews(PLACE_URL, 1000)

    assert len(fake.requests) == 2
    assert result.stopped_because == StopReason.REPEATED_TOKEN


def test_place_with_no_reviews_returns_an_empty_result():
    result = reply(200, NO_RESULTS).fetch_reviews(PLACE_URL, 100)

    assert result.pages == []
    assert result.review_records == []
    assert result.place_details is None
    assert result.reviews_fetched == 0
    assert result.searches_used == 1  # SerpApi counts an empty result as a search
    assert result.stopped_because == StopReason.NO_RESULTS


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_reviews": 0}, "max_reviews"),
        ({"max_reviews": 5, "sort_by": "x"}, "sort_by"),
    ],
)
def test_invalid_arguments_are_refused(kwargs, message):
    with pytest.raises(ValueError, match=message):
        source_with(FakeSerpApi()).fetch_reviews(PLACE_URL, **kwargs)
