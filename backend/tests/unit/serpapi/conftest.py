"""S1-020: fetching Google Maps reviews through SerpApi, with no real network calls.

Every request goes to a fake transport (httpx.MockTransport) that answers with
recorded SerpApi pages or with SerpApi's documented error responses.

Fixtures: two real pages for Smashburger, Newark, sorted newest first, recorded
by the SerpApi spike. Reviewer identities were replaced with fakes there, and
four staff first names inside one review's text are replaced with "[name]".
"""

import json
from pathlib import Path

import httpx

from app.adapters.serpapi import (
    SerpApiReviewSource,
)

FIXTURES = Path(__file__).resolve().parents[3] / "app" / "fixtures"
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
