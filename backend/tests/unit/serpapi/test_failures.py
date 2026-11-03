"""S1-020: timeouts, used-up quota and other provider failures stay distinct."""

import httpx
import pytest

from app.adapters.serpapi import (
    RawPage,
    ReviewFetchTimeoutError,
    ReviewProviderError,
    ReviewQuotaExceededError,
)
from app.repositories.ingestion_runs import ErrorCode
from app.services.normalize_reviews import (
    scrub_page,
)
from tests.unit.serpapi.conftest import (
    PAGE_1,
    PLACE_URL,
    TOKEN_AFTER_PAGE_1,
    raising,
    reply,
    source_with,
)


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
