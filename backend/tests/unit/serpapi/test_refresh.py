"""S3-009: refreshing a target. New reviews first, then older ones from the cursor.

FakePlace is a Google Maps place with any number of reviews, newest first. Its
page tokens are offsets into that list, so new reviews push older ones down, as
they do on Google: a saved token then re-reads a few reviews the target has.
"""

import httpx

from app.adapters.serpapi import OlderCursor, StopReason
from tests.unit.serpapi.conftest import PLACE_URL, source_with


class FakePlace:
    def __init__(self, total, refused_tokens=(), empty_tokens=()):
        self.reviews = [_review(f"old-{i}") for i in range(total)]
        self.refused_tokens = set(refused_tokens)
        self.empty_tokens = set(empty_tokens)
        self.requests: list[httpx.Request] = []

    def post_new_reviews(self, count):
        self.reviews[:0] = [_review(f"new-{i}") for i in range(count)]

    def ids(self, start, stop):
        return {review["review_id"] for review in self.reviews[start:stop]}

    def __call__(self, request):
        self.requests.append(request)
        token = request.url.params.get("next_page_token")
        if token in self.refused_tokens:
            return httpx.Response(400, json={"error": "Invalid next_page_token."})
        if token in self.empty_tokens:
            return httpx.Response(200, json={"reviews": []})
        start = 0 if token is None else int(token.removeprefix("offset-"))
        size = 10 if token is None else int(request.url.params["num"])
        page = self.reviews[start : start + size]
        body = {"search_metadata": {"status": "Success"}, "reviews": page}
        end = start + len(page)
        if end < len(self.reviews):
            body["serpapi_pagination"] = {"next_page_token": f"offset-{end}"}
        return httpx.Response(200, json=body)


def _review(review_id):
    return {"review_id": review_id, "rating": 4, "snippet": f"Review {review_id}"}


def fetch(place, **kwargs):
    return source_with(place).fetch_reviews(PLACE_URL, 100, **kwargs)


def fetched_ids(result):
    return [r["review_id"] for page in result.payloads for r in page["reviews"]]


# --- First ingestion -----------------------------------------------------------------


def test_a_first_ingestion_reads_the_newest_100_and_saves_where_to_continue():
    place = FakePlace(500)

    result = fetch(place)

    assert result.searches_used == 6  # 10 + 20 + 20 + 20 + 20 + 10
    assert result.new_reviews == 100
    assert result.stopped_because == StopReason.CAP_REACHED
    assert result.caught_up is False
    assert result.older_cursor == OlderCursor("offset-100", all_collected=False)


def test_a_first_ingestion_of_a_small_place_marks_every_review_collected():
    result = fetch(FakePlace(30))

    assert result.new_reviews == 30
    assert result.stopped_because == StopReason.NO_MORE_PAGES
    assert result.older_cursor == OlderCursor(None, all_collected=True)


# --- Refresh: new reviews, then older ones -----------------------------------------


def test_a_refresh_with_nothing_new_collects_the_next_100_older_reviews():
    place = FakePlace(500)

    result = fetch(
        place, known_review_ids=place.ids(0, 100), older_page_token="offset-100"
    )

    assert result.caught_up is True
    assert result.resumed_older is True
    assert result.searches_used == 6  # page 1 (all known), then 5 older pages
    assert result.new_reviews == 100
    assert set(fetched_ids(result)) - place.ids(0, 100) == place.ids(100, 200)
    assert result.older_cursor == OlderCursor("offset-200", all_collected=False)


def test_a_refresh_gets_new_reviews_first_then_older_ones():
    place = FakePlace(500)
    known = place.ids(0, 100)
    place.post_new_reviews(4)  # every old review moves down 4 places

    result = fetch(place, known_review_ids=known, older_page_token="offset-100")

    new_ids = set(fetched_ids(result)) - known
    assert {f"new-{i}" for i in range(4)} <= new_ids
    assert result.new_reviews == 100 == len(new_ids)
    assert result.caught_up is True and result.resumed_older is True
    assert result.stopped_because == StopReason.CAP_REACHED


def test_a_page_that_mixes_new_and_known_reviews_is_not_caught_up():
    place = FakePlace(500)
    known = place.ids(0, 100)
    place.post_new_reviews(4)

    result = fetch(place, known_review_ids=known, older_page_token="offset-100")

    # Page 1 holds 4 new reviews, so page 2 is read too before jumping.
    second_request = place.requests[1].url.params
    assert second_request["next_page_token"] == "offset-10"
    assert place.requests[2].url.params["next_page_token"] == "offset-100"
    assert result.caught_up is True


def test_a_review_without_a_review_id_counts_as_new():
    place = FakePlace(500)
    known = place.ids(0, 100)
    del place.reviews[0]["review_id"]

    fetch(place, known_review_ids=known, older_page_token="offset-100")

    # Page 1 is not "all known", so page 2 is read before the jump.
    assert place.requests[1].url.params["next_page_token"] == "offset-10"


def test_a_refresh_that_reaches_the_last_page_marks_every_review_collected():
    place = FakePlace(150)

    result = fetch(
        place, known_review_ids=place.ids(0, 100), older_page_token="offset-100"
    )

    assert result.new_reviews == 50
    assert result.stopped_because == StopReason.NO_MORE_PAGES
    assert result.older_cursor == OlderCursor(None, all_collected=True)


def test_once_every_review_is_collected_a_refresh_only_checks_for_new_ones():
    place = FakePlace(30)

    result = fetch(place, known_review_ids=place.ids(0, 30), all_collected=True)

    assert result.searches_used == 1
    assert result.stopped_because == StopReason.CAUGHT_UP
    assert result.older_cursor is None  # nothing to change


def test_more_new_reviews_than_the_cap_leave_the_older_cursor_alone():
    place = FakePlace(500)
    known = place.ids(0, 100)
    place.post_new_reviews(120)

    result = fetch(place, known_review_ids=known, older_page_token="offset-100")

    assert result.caught_up is False
    assert result.stopped_because == StopReason.CAP_REACHED
    assert result.older_cursor is None  # it never got past the new reviews


# --- Refresh: the saved token does not work ---------------------------------------


def test_a_refused_token_walks_down_from_the_top_instead():
    place = FakePlace(500, refused_tokens={"offset-100"})

    result = fetch(
        place, known_review_ids=place.ids(0, 100), older_page_token="offset-100"
    )

    assert result.resume_rejected is True
    assert result.resumed_older is False
    assert result.new_reviews == 100
    assert set(fetched_ids(result)) - place.ids(0, 100) == place.ids(100, 200)
    assert result.older_cursor == OlderCursor("offset-200", all_collected=False)


def test_an_empty_answer_to_the_saved_token_also_walks_down():
    place = FakePlace(500, empty_tokens={"offset-100"})

    result = fetch(
        place, known_review_ids=place.ids(0, 100), older_page_token="offset-100"
    )

    assert result.resume_rejected is True
    assert result.new_reviews == 100
    assert result.older_cursor.all_collected is False


def test_the_search_limit_stops_a_walk_and_saves_how_far_it_got():
    place = FakePlace(500)

    result = fetch(place, known_review_ids=place.ids(0, 100), max_searches=4)

    assert result.searches_used == 4
    assert result.stopped_because == StopReason.SEARCH_LIMIT
    # Page 1 (offset 0) and walked pages ending at offset 70: continue from there.
    assert result.older_cursor == OlderCursor("offset-70", all_collected=False)


def test_the_search_limit_also_applies_to_a_first_ingestion():
    result = fetch(FakePlace(500), max_searches=2)

    assert result.searches_used == 2
    assert result.stopped_because == StopReason.SEARCH_LIMIT
    assert result.older_cursor == OlderCursor("offset-30", all_collected=False)


def test_tokens_that_loop_back_stop_the_fetch():
    # None -> A -> B -> A ...: without the check this would never end.
    next_of = {None: "A", "A": "B", "B": "A"}
    counter = iter(range(1000))

    def serpapi(request):
        token = request.url.params.get("next_page_token")
        page = [_review(f"r-{next(counter)}") for _ in range(10)]
        body = {
            "reviews": page,
            "serpapi_pagination": {"next_page_token": next_of[token]},
        }
        return httpx.Response(200, json=body)

    result = source_with(serpapi).fetch_reviews(PLACE_URL, 1000)

    assert result.searches_used == 3
    assert result.stopped_because == StopReason.REPEATED_TOKEN
