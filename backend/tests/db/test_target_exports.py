"""S3-017, S3-018, S3-019 through the real endpoints and real Postgres.

Skipped without TEST_DATABASE_URL. What the files contain is tested without a database
in tests/unit/test_export.py; this file checks who can download what, and that the
file is the target's saved dataset.
"""

import csv
import io

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"
MINE = "Blue Bottle - Mint Plaza"


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def make_target(conn):
    """A target with saved reviews and a finished run, saved the way the worker does."""

    def _make(owner_id, name, reviews) -> str:
        target = TargetRepository(conn).create(owner_id, name, "google_maps", URL)
        run_id = conn.execute(
            "insert into public.ingestion_runs (target_id, status, started_at) "
            "values (%s, 'RUNNING', now()) returning id",
            (target["id"],),
        ).fetchone()["id"]
        ReviewRepository(conn).bulk_insert(
            run_id,
            target["id"],
            owner_id,
            [
                ReviewCreate(
                    rating=rating,
                    review_text=text,
                    mapping_version="serpapi@1",
                    source_review_id=f"{name}-{i}",
                )
                for i, (text, rating) in enumerate(reviews)
            ],
        )
        conn.execute(
            "update public.ingestion_runs set status = 'COMPLETED', "
            "finished_at = now(), reviews_ingested = %s where id = %s",
            (len(reviews), run_id),
        )
        return str(target["id"])

    return _make


@pytest.fixture
def world(make_user, make_target):
    owner, stranger = make_user(), make_user()
    return {
        "owner": owner,
        "mine": make_target(owner, MINE, [("Great coffee", 5), ("Slow line", 2)]),
        "strangers": make_target(stranger, "Stranger Cafe", [("Zebra secret", 4)]),
    }


def _export(api, make_token, user, target_id, kind):
    return api.get(
        f"/api/v1/targets/{target_id}/export/{kind}",
        headers=auth(make_token(str(user))),
    )


def test_the_csv_is_the_targets_saved_reviews_under_a_meaningful_name(
    api, make_token, world
):
    response = _export(api, make_token, world["owner"], world["mine"], "reviews")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == (
        'attachment; filename="blue-bottle-mint-plaza-reviews.csv"'
    )
    assert response.headers["cache-control"] == "no-store"
    rows = list(csv.reader(io.StringIO(response.text.removeprefix("﻿"))))
    assert {r[1] for r in rows[1:]} == {"Great coffee", "Slow line"}
    assert {r[3] for r in rows[1:]} == {f"{MINE}-0", f"{MINE}-1"}


def test_the_csv_never_holds_another_users_review(api, make_token, world):
    response = _export(api, make_token, world["owner"], world["mine"], "reviews")

    assert "Zebra secret" not in response.text


def test_the_markdown_describes_the_saved_analysis(api, make_token, world):
    response = _export(api, make_token, world["owner"], world["mine"], "analysis")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert response.headers["content-disposition"] == (
        'attachment; filename="blue-bottle-mint-plaza-analysis.md"'
    )
    assert "# Blue Bottle \\- Mint Plaza" in response.text
    assert "- Saved reviews: 2" in response.text
    assert "- Status: Ingested" in response.text
    assert "Zebra secret" not in response.text


@pytest.mark.parametrize("kind", ["reviews", "analysis"])
def test_another_users_export_is_the_same_404_as_a_missing_target(
    api, make_token, world, kind
):
    response = _export(api, make_token, world["owner"], world["strangers"], kind)

    assert response.status_code == 404
    assert "Zebra secret" not in response.text


@pytest.mark.parametrize("kind", ["reviews", "analysis"])
def test_an_export_needs_a_signed_in_user(api, world, kind):
    response = api.get(f"/api/v1/targets/{world['mine']}/export/{kind}")

    assert response.status_code == 401
