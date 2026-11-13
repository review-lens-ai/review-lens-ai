"""S2-002: one scenario across the boundaries a Sprint 2 question crosses.

Each component has its own tests. These check what could still go wrong when they
are combined: the summary endpoint, the reviews endpoint and the Q&A tools must all
read the same saved dataset of the active target, a switch of target must change
all of them, and another user's data must be unreachable through every one.

Two datasets that are easy to tell apart (S2-018): Target A's reviews are about
battery life, Target B's are about customer service. Skipped without
TEST_DATABASE_URL.
"""

import pytest

from app.core.errors import NotFoundError
from app.db import get_connection
from app.main import app
from app.repositories.review_search import ReviewSearchRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from app.services.qa.tools.toolset import ToolSet
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"

BATTERY = [
    ("The battery life is short, it dies by noon", 2),
    ("Battery life is the one thing I would fix", 2),
    ("Great screen but the battery drains fast", 3),
]
SERVICE = [
    ("Customer service answered within a minute", 5),
    ("Friendly customer service and a quick refund", 5),
    ("Customer service was polite and helpful", 4),
    ("Customer service never called me back", 1),
]
STRANGER = [("Quiet place near the zebra crossing", 4)]


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def make_target(conn):
    """A target with saved reviews, saved the way the worker does."""

    def _make(owner_id, name, reviews) -> str:
        target = TargetRepository(conn).create(owner_id, name, "google_maps", URL)
        if not reviews:  # never ingested: no run, no reviews
            return str(target["id"])
        run_id = conn.execute(
            """
            insert into public.ingestion_runs (target_id, status, started_at)
            values (%s, 'RUNNING', now())
            returning id
            """,
            (target["id"],),
        ).fetchone()["id"]
        ReviewRepository(conn).bulk_insert(
            run_id,
            target["id"],
            owner_id,
            [
                ReviewCreate(
                    rating=rating, review_text=text, mapping_version="serpapi@1"
                )
                for text, rating in reviews
            ],
        )
        conn.execute(
            """
            update public.ingestion_runs
               set status = 'COMPLETED', finished_at = now(), reviews_ingested = %s
             where id = %s
            """,
            (len(reviews), run_id),
        )
        return str(target["id"])

    return _make


@pytest.fixture
def world(make_user, make_target):
    owner, stranger = make_user(), make_user()
    return {
        "owner": owner,
        "stranger": stranger,
        "battery": make_target(owner, "Battery Shop", BATTERY),
        "service": make_target(owner, "Service Cafe", SERVICE),
        "empty": make_target(owner, "Empty Place", []),
        "strangers": make_target(stranger, "Stranger's Place", STRANGER),
    }


def _get(api, make_token, user, path):
    return api.get(f"/api/v1/targets/{path}", headers=auth(make_token(str(user))))


def _tools(conn, owner, target_id) -> ToolSet:
    """What the endpoint makes for one question: a fresh tool set for the active target."""
    return ToolSet(ReviewSearchRepository(conn), owner, target_id)


def _dataset(api, make_token, owner, target_id):
    """What the app shows for a target: the summary and the first page of reviews."""
    summary = _get(api, make_token, owner, f"{target_id}/summary").json()
    page = _get(api, make_token, owner, f"{target_id}/reviews").json()
    return summary, page


# --- The three consumers read the same saved dataset (S2-BR-007) ---


def test_the_summary_the_reviews_list_and_the_q_and_a_tools_agree(
    api, conn, world, make_token
):
    owner, target_id = world["owner"], world["battery"]

    summary, page = _dataset(api, make_token, owner, target_id)
    stats = _tools(conn, owner, target_id).registry.run("review_stats", {})

    assert summary["review_count"] == page["total"] == len(BATTERY)
    assert summary["average_rating"] == 2.33
    assert f"{len(BATTERY)} reviews, average 2.33 out of 5." in stats.text
    assert {r["review_text"] for r in page["items"]} == {t for t, _ in BATTERY}


# --- Switching the active target changes what every consumer returns ---


def test_switching_target_changes_the_summary_the_list_and_the_tools(
    api, conn, world, make_token
):
    owner = world["owner"]

    for target_id, reviews, theme, other_theme in [
        (world["battery"], BATTERY, "battery", "customer service"),
        (world["service"], SERVICE, "customer service", "battery"),
    ]:
        summary, page = _dataset(api, make_token, owner, target_id)
        tools = _tools(conn, owner, target_id).registry

        assert summary["review_count"] == len(reviews)
        assert {r["review_text"] for r in page["items"]} == {t for t, _ in reviews}
        found = tools.run("search_reviews", {"words": theme, "limit": 10})
        assert len(found.sources) == len(reviews)
        assert tools.run("search_reviews", {"words": other_theme}).text == (
            "No reviews match."
        )


def test_nothing_of_the_previous_target_carries_over_after_a_switch(conn, world):
    owner = world["owner"]
    first = _tools(conn, owner, world["battery"])
    shown = first.registry.run("search_reviews", {"words": "battery", "limit": 10})
    assert [s.label for s in shown.sources] == ["R1", "R2", "R3"]

    second = _tools(conn, owner, world["service"])
    result = second.registry.run("search_reviews", {"words": "service", "limit": 10})

    # New labels, none of the first target's reviews, and a label from the first
    # question means nothing to the second.
    assert [s.label for s in result.sources] == ["R1", "R2", "R3", "R4"]
    assert not {s.review_id for s in shown.sources} & {
        s.review_id for s in result.sources
    }
    stale = second.registry.run("get_reviews", {"labels": ["R1"]})
    assert "battery" not in stale.text.lower()
    assert [s.review_id for s in stale.sources] == [result.sources[0].review_id]


# --- Ownership applies before any review is retrieved (S2-BR-022, S2-BR-026) ---


def test_another_users_target_is_unreachable_through_every_consumer(
    api, conn, world, make_token
):
    stranger, target_id = world["stranger"], world["battery"]

    summary = _get(api, make_token, stranger, f"{target_id}/summary")
    reviews = _get(api, make_token, stranger, f"{target_id}/reviews")
    assert (summary.status_code, reviews.status_code) == (404, 404)
    assert "battery" not in summary.text + reviews.text

    tools = _tools(conn, stranger, target_id).registry
    for name, arguments in [
        ("review_stats", {}),
        ("search_reviews", {"words": "battery"}),
        ("top_terms", {}),
    ]:
        with pytest.raises(NotFoundError):
            tools.run(name, arguments)


def test_nobody_can_point_their_tools_at_another_users_target(conn, world):
    # The other direction as well: the owner aims a tool set at the stranger's target.
    tools = _tools(conn, world["owner"], world["strangers"]).registry

    for name, arguments in [
        ("review_stats", {}),
        ("search_reviews", {"words": "zebra"}),
        ("top_terms", {}),
    ]:
        with pytest.raises(NotFoundError):
            tools.run(name, arguments)


def test_no_tool_finds_the_strangers_review_in_any_of_the_owners_targets(conn, world):
    # The stranger's distinctive phrase must not turn up anywhere in the owner's data.
    for target_id in (world["battery"], world["service"]):
        result = _tools(conn, world["owner"], target_id).registry.run(
            "search_reviews", {"words": "zebra crossing"}
        )

        assert result.text == "No reviews match."
        assert result.sources == []


# --- A target with no saved reviews gives no review-grounded data (S2-BR-025) ---


def test_a_target_without_reviews_has_an_empty_dataset_everywhere(
    api, conn, world, make_token
):
    owner, target_id = world["owner"], world["empty"]

    summary, page = _dataset(api, make_token, owner, target_id)
    tools = _tools(conn, owner, target_id).registry

    assert summary["review_count"] == 0 and summary["average_rating"] is None
    assert page["total"] == 0 and page["items"] == []
    assert tools.run("review_stats", {}).text == "No reviews match."
    assert tools.run("search_reviews", {}).text == "No reviews match."
