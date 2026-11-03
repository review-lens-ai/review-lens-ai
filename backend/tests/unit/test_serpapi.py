"""S1-020: fetching Google Maps reviews through SerpApi, with no real network calls.

Every request goes to a fake transport (httpx.MockTransport) that answers with
recorded SerpApi pages or with SerpApi's documented error responses.

Fixtures: two real pages for Smashburger, Newark, sorted newest first, recorded
by the SerpApi spike. Reviewer identities were replaced with fakes there, and
four staff first names inside one review's text are replaced with "[name]".
"""

import json
import logging
import traceback
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.serpapi import (
    SERPAPI_URL,
    RawPage,
    ReviewFetchTimeoutError,
    ReviewProviderConfigError,
    ReviewProviderError,
    ReviewQuotaExceededError,
    SerpApiReviewSource,
    StopReason,
    UnsupportedSourceUrlError,
    extract_data_id,
    pages_planned,
    resolve_data_id,
)
from app.repositories.ingestion_runs import ErrorCode
from app.services.normalize_reviews import (
    MAPPING_VERSION,
    RejectionReason,
    normalize_pages,
    scrub_page,
)

FIXTURES = Path(__file__).resolve().parents[2] / "app" / "fixtures"
PAGE_1 = json.loads(
    (FIXTURES / "serpapi_reviews_with_missing_text.json").read_text(encoding="utf-8")
)
PAGE_2 = json.loads(
    (FIXTURES / "serpapi_reviews_next_page.json").read_text(encoding="utf-8")
)
TOKEN_AFTER_PAGE_1 = PAGE_1["serpapi_pagination"]["next_page_token"]
TOKEN_AFTER_PAGE_2 = PAGE_2["serpapi_pagination"]["next_page_token"]

API_KEY = "test-key-0123456789abcdef"
DATA_ID = "0x89c2537da9b3163d:0x2be30fcff506fec8"
PLACE_URL = (
    "https://www.google.com/maps/place/Smashburger/@40.742,-74.178,17z"
    f"/data=!3m1!4b1!4m6!3m5!1s{DATA_ID}!8m2!3d40.742!4d-74.178"
)
SHORT_URL = "https://maps.app.goo.gl/AbC123xyz"
NO_RESULTS = {
    "search_metadata": {"status": "Success"},
    "error": "Google hasn't returned any results for this query.",
}


class FakeSerpApi:
    """Answers like SerpApi: page 1 without a token, page 2 for page 1's token."""

    def __init__(self, page_2=PAGE_2):
        self.page_2 = page_2
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        token = request.url.params.get("next_page_token")
        if token is None:
            return httpx.Response(200, json=PAGE_1)
        if token == TOKEN_AFTER_PAGE_1:
            return httpx.Response(200, json=self.page_2)
        return httpx.Response(200, json={"reviews": []})


def source_with(handler) -> SerpApiReviewSource:
    return SerpApiReviewSource(
        API_KEY, httpx.Client(transport=httpx.MockTransport(handler))
    )


def reply(status: int, body) -> SerpApiReviewSource:
    """A source whose every request gets the same response."""
    return source_with(lambda request: httpx.Response(status, json=body))


def raising(error_class) -> SerpApiReviewSource:
    def handler(request):
        raise error_class("simulated", request=request)

    return source_with(handler)


# --- Finding the place --------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        PLACE_URL,
        PLACE_URL.replace(":", "%3A"),  # colon percent-encoded in the data= part
        f"https://maps.google.com/?ftid={DATA_ID}",
        PAGE_1["search_metadata"]["google_maps_reviews_url"],  # a real SerpApi link
    ],
)
def test_place_id_is_read_from_a_full_google_maps_link(url):
    assert extract_data_id(url) == DATA_ID


@pytest.mark.parametrize(
    "url",
    [
        SHORT_URL,
        "https://www.google.com/maps/search/smashburger+newark",
        "https://maps.google.com/?cid=3162874652871540424",
    ],
)
def test_links_without_a_place_id_give_none(url):
    assert extract_data_id(url) is None


def test_short_link_is_followed_to_the_place():
    def handler(request):
        if request.url.host == "maps.app.goo.gl":
            return httpx.Response(302, headers={"location": PLACE_URL})
        raise AssertionError("must stop once the place id is found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert resolve_data_id(SHORT_URL, client) == DATA_ID


def test_short_link_that_leads_outside_google_is_refused():
    requested_hosts = []

    def handler(request):
        requested_hosts.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UnsupportedSourceUrlError):
        resolve_data_id(SHORT_URL, client)
    assert requested_hosts == ["maps.app.goo.gl"]  # never calls the outside host


def test_short_link_that_never_reaches_a_place_is_refused():
    def handler(request):
        return httpx.Response(302, headers={"location": SHORT_URL})  # loops

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UnsupportedSourceUrlError):
        resolve_data_id(SHORT_URL, client)


def test_dead_short_link_is_refused():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(UnsupportedSourceUrlError):
        resolve_data_id(SHORT_URL, client)


def test_link_without_a_place_is_refused_without_any_request():
    fake = FakeSerpApi()
    with pytest.raises(UnsupportedSourceUrlError) as caught:
        source_with(fake).fetch_reviews(
            "https://www.google.com/maps/search/smashburger", 100
        )
    assert fake.requests == []
    assert caught.value.error_code == ErrorCode.SOURCE_UNREACHABLE


# --- Fetching pages -----------------------------------------------------------


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


# --- Three distinct failures --------------------------------------------------


@pytest.mark.parametrize("error_class", [httpx.ReadTimeout, httpx.ConnectTimeout])
def test_timeout_is_its_own_failure(error_class):
    with pytest.raises(ReviewFetchTimeoutError) as caught:
        raising(error_class).fetch_reviews(PLACE_URL, 100)
    assert caught.value.error_code == ErrorCode.TIMEOUT


@pytest.mark.parametrize(
    "message",
    [
        "Your account has run out of searches.",
        "The number of requests sent using this API key exceeds the hourly "
        "throughput limit.",
    ],
)
def test_used_up_quota_is_its_own_failure(message):
    with pytest.raises(ReviewQuotaExceededError) as caught:
        reply(429, {"error": message}).fetch_reviews(PLACE_URL, 100)
    assert caught.value.error_code == ErrorCode.QUOTA_EXCEEDED


@pytest.mark.parametrize(
    "source",
    [
        lambda: reply(500, {"error": "Internal error"}),
        lambda: reply(503, None),
        lambda: reply(400, {"error": "Missing query `data_id` parameter."}),
        lambda: reply(200, {"error": "Something unexpected."}),
        lambda: reply(200, {"search_metadata": {"status": "Error"}}),
        lambda: source_with(lambda r: httpx.Response(200, text="<html>not json")),
        lambda: raising(httpx.ConnectError),
    ],
)
def test_other_provider_failures_are_provider_errors(source):
    with pytest.raises(ReviewProviderError) as caught:
        source().fetch_reviews(PLACE_URL, 100)
    assert type(caught.value) is ReviewProviderError
    assert caught.value.error_code == ErrorCode.SOURCE_UNREACHABLE


def test_failure_keeps_the_pages_fetched_before_it():
    def handler(request):
        if request.url.params.get("next_page_token") is None:
            return httpx.Response(200, json=PAGE_1)
        raise httpx.ReadTimeout("simulated", request=request)

    with pytest.raises(ReviewFetchTimeoutError) as caught:
        source_with(handler).fetch_reviews(PLACE_URL, 100)
    assert caught.value.pages == [RawPage(1, scrub_page(PAGE_1), TOKEN_AFTER_PAGE_1)]


# --- The API key --------------------------------------------------------------


@pytest.mark.parametrize("settings_key", [None, SecretStr("")])
def test_missing_api_key_fails_before_any_request(settings_key):
    settings = SimpleNamespace(review_provider_api_key=settings_key)
    with pytest.raises(ReviewProviderConfigError) as caught:
        SerpApiReviewSource.from_settings(settings)
    assert caught.value.error_code == ErrorCode.UNEXPECTED


def test_api_key_is_read_from_settings():
    fake = FakeSerpApi()
    settings = SimpleNamespace(review_provider_api_key=SecretStr(API_KEY))
    client = httpx.Client(transport=httpx.MockTransport(fake))

    SerpApiReviewSource.from_settings(settings, client).fetch_reviews(PLACE_URL, 10)

    assert fake.requests[0].url.params["api_key"] == API_KEY


def test_rejected_api_key_is_a_setup_error():
    invalid = {"error": "Invalid API key. Your API key should be here: https://..."}
    with pytest.raises(ReviewProviderConfigError) as caught:
        reply(401, invalid).fetch_reviews(PLACE_URL, 100)
    assert caught.value.error_code == ErrorCode.UNEXPECTED


@pytest.mark.parametrize(
    "source",
    [
        lambda: raising(httpx.ReadTimeout),
        lambda: raising(httpx.ConnectError),
        lambda: reply(429, {"error": f"Out of searches for {API_KEY}"}),
        lambda: reply(500, {"error": f"Bad key {API_KEY}"}),
    ],
)
def test_api_key_never_appears_in_errors(source):
    with pytest.raises(Exception) as caught:
        source().fetch_reviews(PLACE_URL, 100)
    error = caught.value
    # The full traceback a logger would print, including any chained httpx
    # error (httpx errors hold the request URL, which holds the key).
    logged = "".join(traceback.format_exception(error))
    assert API_KEY not in logged
    assert API_KEY not in repr(error)


def test_api_key_is_removed_from_returned_pages():
    echoed = {**PAGE_1, "search_parameters": {**PAGE_1["search_parameters"]}}
    echoed["search_parameters"]["api_key"] = API_KEY

    result = reply(200, echoed).fetch_reviews(PLACE_URL, 10)

    assert API_KEY not in json.dumps(result.payloads)
    assert result.payloads[0]["search_parameters"]["api_key"] == "REDACTED"


def test_api_key_never_reaches_the_logs(caplog):
    caplog.set_level(logging.DEBUG)

    source_with(FakeSerpApi()).fetch_reviews(PLACE_URL, 30)

    assert API_KEY not in caplog.text


def test_api_key_is_hidden_in_repr():
    assert API_KEY not in repr(source_with(FakeSerpApi()))
