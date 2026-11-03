"""S1-020: the API key stays on the server."""

import json
import logging
import traceback
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app.adapters.serpapi import (
    ReviewProviderConfigError,
    SerpApiReviewSource,
)
from app.repositories.ingestion_runs import ErrorCode
from tests.unit.serpapi.conftest import (
    API_KEY,
    PAGE_1,
    PLACE_URL,
    FakeSerpApi,
    raising,
    reply,
    source_with,
)


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
