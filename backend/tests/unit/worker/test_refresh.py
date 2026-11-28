"""S3-009: the worker refreshes a target that already has reviews.

Page 1 of the fixture holds 10 reviews (5 rating-only), page 2 holds 20 (7
rating-only). How the adapter walks the pages is tested in
tests/unit/serpapi/test_refresh.py; these tests check what the worker passes in,
what it saves, and when.
"""

import httpx

from app.adapters.serpapi import SerpApiReviewSource
from app.worker import process_next_run
from tests.unit.worker.conftest import (
    API_KEY,
    PAGE_1,
    PAGE_2,
    FakeGoogleAndSerpApi,
    FakeReviews,
    FakeTargets,
    make_pipeline,
)

PAGE_1_IDS = {review["review_id"] for review in PAGE_1["reviews"]}
PAGE_2_IDS = {review["review_id"] for review in PAGE_2["reviews"]}
TOKEN_AFTER_PAGE_2 = PAGE_2["serpapi_pagination"]["next_page_token"]


def test_a_first_run_saves_where_the_next_one_continues(log):
    pipeline, _ = make_pipeline(log, max_reviews=30)

    process_next_run(pipeline)

    assert pipeline.runs.metadata["known_reviews_before"] == 0
    assert pipeline.runs.metadata["stopped_because"] == "CAP_REACHED"
    assert pipeline.targets.saved_cursor == (TOKEN_AFTER_PAGE_2, False)


def test_an_up_to_date_refresh_costs_one_search_and_adds_nothing(log):
    # Every page-1 review was seen before: 5 saved, 5 rating-only rejected.
    reviews = FakeReviews(log, already_saved=5, seen=PAGE_1_IDS)
    targets = FakeTargets(log, place_id="known", all_reviews_collected=True)
    pipeline, google = make_pipeline(log, reviews=reviews, targets=targets)

    process_next_run(pipeline)

    assert google.searches == 1
    assert pipeline.runs.metadata["stopped_because"] == "CAUGHT_UP"
    assert pipeline.runs.metadata["known_reviews_before"] == 10
    # The 5 rating-only reviews were rejected before: duplicates now, not rejected.
    assert pipeline.reviews.rejected == []
    assert pipeline.runs.result == ("SUCCEEDED", 0, 0, 10)
    assert pipeline.targets.saved_cursor is None  # nothing older to move to


def test_a_refresh_jumps_to_the_saved_cursor_and_moves_it(log):
    asked = []

    def serpapi(request):
        token = request.url.params.get("next_page_token")
        asked.append(token)
        if token is None:
            return httpx.Response(200, json=PAGE_1)
        # The saved cursor leads to page 2, the place's last page.
        return httpx.Response(200, json={**PAGE_2, "serpapi_pagination": {}})

    targets = FakeTargets(log, place_id="known", older_page_token="saved-token")
    reviews = FakeReviews(log, seen=PAGE_1_IDS)
    pipeline, _ = make_pipeline(
        log, targets=targets, reviews=reviews, open_source=_source_answering(serpapi)
    )

    process_next_run(pipeline)

    # Page 1 is all known, so the second search is the jump to the saved cursor.
    assert asked == [None, "saved-token"]
    assert pipeline.runs.metadata["resumed_older"] is True
    # The jump reached the last page: every older review is now collected.
    assert pipeline.targets.saved_cursor == (None, True)


def test_a_failed_refresh_leaves_the_cursor_where_it_was(log):
    timeout = httpx.ReadTimeout("simulated")
    google = FakeGoogleAndSerpApi(log, page_2=timeout)
    pipeline, _ = make_pipeline(log, google=google)

    process_next_run(pipeline)

    assert pipeline.runs.result[0] == "FAILED"
    assert pipeline.targets.saved_cursor is None


def _source_answering(handler):
    return lambda: SerpApiReviewSource(
        API_KEY, httpx.Client(transport=httpx.MockTransport(handler))
    )
