"""S3-013: supporting evidence with grounded answers, through POST /questions.

The rules (S3-BR-020 to S3-BR-022): a grounded answer can show supporting reviews;
they come only from the active AnalysisTarget's stored reviews; and their text and
details are never made up.

How it works (app/services/qa/answer.py, build_answer): the model never returns
review text. It cites labels (R1, R2, ...) that a tool handed out for this
question. The LabelBook maps each label back to a stored review id, the reviews are
read again from the database through the owner-checked search, and that stored row
is what the user sees. A label no tool handed out is dropped, duplicates are
removed, and at most MAX_CITATIONS (10) are kept.

The real route, auth, repositories, tools and SQL run here; only the model is the
scripted FakeChatModel. Skipped without TEST_DATABASE_URL.
"""

from datetime import UTC, datetime

import pytest

from app.api.questions import get_model_factory
from app.db import get_connection
from app.main import app
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from app.services.qa.answer import MAX_CITATIONS
from tests.conftest import auth
from tests.unit.qa.conftest import FakeChatModel, call, submit

URL = "https://www.google.com/maps/place/Joes+Pizza"
LOWEST = {"order": "lowest", "limit": 10}
HIGHEST = {"order": "highest", "limit": 10}

# The fields a piece of evidence has: the stored review's own, and nothing about
# the reviewer (no name, profile or photo is ever stored).
EVIDENCE_FIELDS = {"id", "rating", "review_text", "reviewed_at"}


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


def _save(conn, owner_id, name: str, texts: list[str]) -> str:
    """A target whose reviews are rated 1, 2, 3, ... in the order given."""
    target = TargetRepository(conn).create(owner_id, name, "google_maps", URL)
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
                rating=min(i + 1, 5),
                review_text=text,
                mapping_version="serpapi@1",
                reviewed_at=datetime(2026, 1, i + 1, tzinfo=UTC),
            )
            for i, text in enumerate(texts)
        ],
    )
    return str(target["id"])


@pytest.fixture
def world(conn, make_user, make_token):
    """User A's active target, User A's other target, and User B's target."""
    owner, other = make_user(), make_user()
    return {
        "target": _save(
            conn, owner, "Joe's", ["Cold fries", "Slow service", "Great crust"]
        ),
        "second": _save(conn, owner, "Joe's 2", ["Second place review"]),
        "theirs": _save(conn, other, "Bella's", ["User B's private review"]),
        "headers": auth(make_token(str(owner))),
    }


def ask(api, world, model, target="target"):
    app.dependency_overrides[get_model_factory] = lambda: lambda: model
    response = api.post(
        f"/api/v1/targets/{world[target]}/questions",
        json={"question": "What do people say?"},
        headers=world["headers"],
    )
    assert response.status_code == 200, response.text
    return response.json()


def _stored(conn, review_id: str) -> dict:
    return conn.execute(
        """
        select target_id::text, rating, review_text, reviewed_at
          from public.reviews where id = %s
        """,
        (review_id,),
    ).fetchone()


def test_every_piece_of_evidence_is_a_stored_review_of_the_active_target(
    api, world, conn
):
    model = FakeChatModel(
        call("search_reviews", **LOWEST),
        submit("ANSWERED", "Fries and service get complaints.", cited=["R1", "R2"]),
    )

    body = ask(api, world, model)

    assert body["status"] == "ANSWERED"
    assert len(body["evidence"]) == 2
    for evidence in body["evidence"]:
        row = _stored(conn, evidence["id"])
        assert row["target_id"] == world["target"]
        # Text, rating and date are the stored row's, exactly.
        assert evidence["review_text"] == row["review_text"]
        assert evidence["rating"] == row["rating"]
        assert datetime.fromisoformat(evidence["reviewed_at"]) == row["reviewed_at"]


def test_evidence_follows_the_citation_order(api, world):
    model = FakeChatModel(
        call("search_reviews", **LOWEST),  # R1 Cold fries, R2 Slow, R3 Great crust
        submit("ANSWERED", "Mixed.", cited=["R3", "R1"]),
    )

    body = ask(api, world, model)

    assert [e["review_text"] for e in body["evidence"]] == ["Great crust", "Cold fries"]


def test_evidence_carries_only_review_fields_never_reviewer_details(api, world):
    model = FakeChatModel(
        call("search_reviews", **LOWEST),
        submit("ANSWERED", "Fries are cold.", cited=["R1"]),
    )

    [evidence] = ask(api, world, model)["evidence"]

    assert set(evidence) == EVIDENCE_FIELDS


def test_the_models_wording_never_replaces_the_stored_text(api, world):
    # The model quotes a review wrongly in its answer; the evidence still shows the
    # real stored text, because evidence is read from the database, not the model.
    model = FakeChatModel(
        call("search_reviews", **LOWEST),
        submit("ANSWERED", 'A reviewer wrote "the fries were amazing".', cited=["R1"]),
    )

    [evidence] = ask(api, world, model)["evidence"]

    assert evidence["review_text"] == "Cold fries"


def test_a_label_no_tool_showed_is_dropped(api, world):
    model = FakeChatModel(
        call("search_reviews", **LOWEST),
        submit("ANSWERED", "Fries are cold.", cited=["R1", "R7"]),  # R7 never shown
    )

    body = ask(api, world, model)

    assert [e["review_text"] for e in body["evidence"]] == ["Cold fries"]


@pytest.mark.parametrize("other", ["second", "theirs"])
def test_a_review_of_another_target_can_never_be_evidence(api, world, conn, other):
    # The model names another target's review directly (its id, not a label from
    # this question). Only labels count, so it is dropped, and an answer with no
    # valid evidence becomes "not enough evidence".
    outside_id = conn.execute(
        "select id::text from public.reviews where target_id = %s",
        (world[other],),
    ).fetchone()["id"]
    model = FakeChatModel(
        call("search_reviews", **LOWEST),
        submit("ANSWERED", "Something else.", cited=[outside_id]),
    )

    body = ask(api, world, model)

    assert body["status"] == "INSUFFICIENT_EVIDENCE"
    assert body["evidence"] == []


def test_an_answer_with_no_valid_evidence_is_not_shown_as_answered(api, world):
    model = FakeChatModel(
        call("search_reviews", **LOWEST),
        submit("ANSWERED", "People love it.", cited=[]),
    )

    body = ask(api, world, model)

    assert body["status"] == "INSUFFICIENT_EVIDENCE"
    assert body["evidence"] == []


def test_a_refusal_carries_no_evidence(api, world):
    model = FakeChatModel(
        call("search_reviews", **LOWEST),
        submit(
            "OUT_OF_SCOPE",
            "I can only answer about these reviews.",
            cited=["R1"],
            refusal_reason="GENERAL_KNOWLEDGE",
        ),
    )

    body = ask(api, world, model)

    assert body["status"] == "OUT_OF_SCOPE"
    assert body["evidence"] == []


def test_at_most_ten_reviews_are_shown_as_evidence(api, world, conn):
    owner = conn.execute(
        "select owner_id from public.analysis_targets where id = %s",
        (world["target"],),
    ).fetchone()["owner_id"]
    texts = [f"Review number {n}" for n in range(12)]
    world = {**world, "big": _save(conn, owner, "Joe's big", texts)}
    model = FakeChatModel(
        call("search_reviews", **LOWEST),  # R1-R10
        call("search_reviews", **HIGHEST),  # the 2 not yet shown become R11, R12
        submit("ANSWERED", "Lots to say.", cited=[f"R{n}" for n in range(1, 13)]),
    )

    body = ask(api, world, model, target="big")

    assert body["context"]["reviews_considered"] == 12  # all 12 were shown and cited
    assert len(body["evidence"]) == MAX_CITATIONS == 10
    assert len({e["id"] for e in body["evidence"]}) == 10
