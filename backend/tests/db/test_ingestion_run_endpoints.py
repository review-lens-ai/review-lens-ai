"""S1-023 endpoint + real SQL: the workspace can read a target's latest ingestion
result, and only the target's owner can. Skipped without TEST_DATABASE_URL."""

import pytest

from app.db import get_connection
from app.main import app
from app.repositories.ingestion_runs import ErrorCode, IngestionRunRepository
from app.repositories.targets import TargetRepository
from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def setup(conn, make_user, make_token):
    """User A owns one target; user B exists but owns nothing."""
    user_a, user_b = make_user(), make_user()
    target = TargetRepository(conn).create(user_a, "Joe's Pizza", "google_maps", URL)
    return {
        "target_id": target["id"],
        "user_a": user_a,
        "token_a": make_token(str(user_a)),
        "token_b": make_token(str(user_b)),
        "runs": IngestionRunRepository(conn),
    }


def _start(setup):
    """Create a run and move it to running, as the pipeline would."""
    runs = setup["runs"]
    return runs.mark_running(runs.create_run(setup["target_id"], setup["user_a"])["id"])


def _latest(api, target_id, token):
    return api.get(
        f"/api/v1/targets/{target_id}/ingestion-runs/latest", headers=auth(token)
    )


def test_never_ingested_target_has_no_run(api, setup):
    r = _latest(api, setup["target_id"], setup["token_a"])
    assert r.status_code == 200
    assert r.json() == {"run": None}


def test_queued_run_is_reported_as_queued(api, setup):
    setup["runs"].create_run(setup["target_id"], setup["user_a"])

    r = _latest(api, setup["target_id"], setup["token_a"])
    assert r.status_code == 200
    assert r.json()["run"]["status"] == "QUEUED"
    assert r.json()["run"]["finished_at"] is None


def test_running_run_is_reported_as_running(api, setup):
    _start(setup)

    run = _latest(api, setup["target_id"], setup["token_a"]).json()["run"]
    assert run["status"] == "RUNNING"
    assert run["finished_at"] is None


def test_successful_run_reports_status_and_review_count(api, setup):
    runs = setup["runs"]
    run = _start(setup)
    runs.mark_succeeded(run["id"], reviews_ingested=42)

    body = _latest(api, setup["target_id"], setup["token_a"]).json()["run"]
    assert body["status"] == "COMPLETED"
    assert body["reviews_ingested"] == 42
    assert body["reviews_rejected"] == 0
    assert body["error_code"] is None
    assert body["finished_at"] is not None


def test_run_with_skipped_reviews_is_reported_as_partial(api, setup):
    run = _start(setup)
    setup["runs"].mark_succeeded(run["id"], reviews_ingested=40, reviews_rejected=2)

    body = _latest(api, setup["target_id"], setup["token_a"]).json()["run"]
    assert body["status"] == "PARTIAL"
    assert body["reviews_rejected"] == 2


def test_failed_run_reports_only_the_safe_error_code(api, setup):
    runs = setup["runs"]
    run = _start(setup)
    runs.mark_failed(
        run["id"], ErrorCode.SOURCE_UNREACHABLE, detail="ConnectTimeout to serpapi"
    )

    r = _latest(api, setup["target_id"], setup["token_a"])
    body = r.json()["run"]
    assert body["status"] == "FAILED"
    assert body["error_code"] == "SOURCE_UNREACHABLE"
    assert "serpapi" not in r.text.lower()


def test_user_b_gets_not_found_for_user_a_ingestion_result(api, setup):
    runs = setup["runs"]
    run = _start(setup)
    runs.mark_succeeded(run["id"], reviews_ingested=42)

    r = _latest(api, setup["target_id"], setup["token_b"])
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert "42" not in r.text


def test_signed_out_request_is_rejected(api, setup):
    r = api.get(f"/api/v1/targets/{setup['target_id']}/ingestion-runs/latest")
    assert r.status_code == 401
