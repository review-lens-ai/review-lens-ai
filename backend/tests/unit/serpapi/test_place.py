"""S1-020: finding the place behind a Google Maps link."""

import httpx
import pytest

from app.adapters.serpapi import (
    UnsupportedSourceUrlError,
    extract_data_id,
    resolve_data_id,
)
from app.repositories.ingestion_runs import ErrorCode
from tests.unit.serpapi.conftest import (
    DATA_ID,
    PAGE_1,
    PLACE_URL,
    SHORT_URL,
    FakeSerpApi,
    source_with,
)


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
