"""S1-013: user B cannot read, create, change or ingest into user A's review data.

Reviews, runs and rejections have no owner column; they belong to whoever owns
their target. Every route under /api/v1/targets/{target_id} must therefore check
the target's owner first and answer another user's target with a 404.

The 401 for a missing token is covered for every route by
tests/auth/test_routes.py. Skipped without TEST_DATABASE_URL.
"""

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.ingestion_runs import IngestionRunRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from app.worker import process_next_run
from tests.conftest import auth
from tests.db.ingestion_worker.conftest import PLACE_URL, make_pipeline, serpapi

REVIEW_TEXTS = ["Private review one", "Private review two"]

# Every API route that acts on one target or its children (reviews, runs), read
# from the OpenAPI schema like tests/auth/test_routes.py does.
TARGET_ROUTES = sorted(
    (method.upper(), path)
    for path, operations in app.openapi()["paths"].items()
    if path.startswith("/api/v1/") and "{target_id}" in path
    for method in operations
)


@pytest.fixture
def api(client, conn):
    """The real endpoints and repositories, on the test database (rolled back after)."""
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def user_a_data(conn, make_user, make_token):
    """User A owns a target with two saved reviews from a finished run.
    User B is signed in but owns nothing."""
    user_a, user_b = make_user(), make_user()
    target = TargetRepository(conn).create(
        user_a, "A's place", "google_maps", PLACE_URL
    )
    runs = IngestionRunRepository(conn)
    run = runs.mark_running(runs.create_run(target["id"], user_a)["id"])
    reviews = [
        ReviewCreate(rating=4, review_text=text, mapping_version="serpapi@1")
        for text in REVIEW_TEXTS
    ]
    ReviewRepository(conn).bulk_insert(run["id"], target["id"], user_a, reviews)
    runs.mark_succeeded(run["id"], reviews_ingested=len(reviews))
    return {
        "target_id": str(target["id"]),
        "user_a": user_a,
        "token_b": make_token(str(user_b)),
    }


def _snapshot(conn, target_id):
    """What user B must not be able to change: the target, its runs, its reviews."""
    row = conn.execute(
        """
        select (select count(*) from public.analysis_targets where id = %(t)s) as targets,
               (select count(*) from public.ingestion_runs where target_id = %(t)s) as runs,
               (select array_agg(review_text order by review_text)
                  from public.reviews where target_id = %(t)s) as reviews
        """,
        {"t": target_id},
    ).fetchone()
    return dict(row)


def test_the_sweep_covers_the_review_and_ingestion_routes():
    # Guards against the sweep below silently testing nothing.
    assert {
        ("GET", "/api/v1/targets/{target_id}"),
        ("DELETE", "/api/v1/targets/{target_id}"),
        ("GET", "/api/v1/targets/{target_id}/reviews"),
        ("POST", "/api/v1/targets/{target_id}/ingestion-runs"),
        ("GET", "/api/v1/targets/{target_id}/ingestion-runs/latest"),
    } <= set(TARGET_ROUTES)


@pytest.mark.parametrize(("method", "path"), TARGET_ROUTES)
def test_user_b_gets_404_on_every_route_for_user_a_target_and_changes_nothing(
    api, conn, user_a_data, method, path
):
    # A new route that needs a request body would answer 422 here: give it one.
    before = _snapshot(conn, user_a_data["target_id"])

    r = api.request(
        method,
        path.format(target_id=user_a_data["target_id"]),
        headers=auth(user_a_data["token_b"]),
    )

    assert r.status_code == 404
    assert list(r.json()) == ["error"]
    for text in [*REVIEW_TEXTS, "A's place"]:
        assert text not in r.text
    assert _snapshot(conn, user_a_data["target_id"]) == before


def test_user_b_starting_ingestion_on_user_a_target_creates_no_run_and_no_reviews(
    api, conn, user_a_data
):
    open_source, serpapi_calls = serpapi()
    pipeline = make_pipeline(conn, open_source, reviews=ReviewRepository(conn))
    before = _snapshot(conn, user_a_data["target_id"])

    r = api.post(
        f"/api/v1/targets/{user_a_data['target_id']}/ingestion-runs",
        headers=auth(user_a_data["token_b"]),
    )

    assert r.status_code == 404
    # Nothing was queued, so the worker has nothing to run: the provider is never
    # called and no review can be created under user A's target.
    assert process_next_run(pipeline) is False
    assert serpapi_calls == []
    assert _snapshot(conn, user_a_data["target_id"]) == before
