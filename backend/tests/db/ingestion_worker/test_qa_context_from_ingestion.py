"""S2-003: Q&A context built from reviews the real worker ingested.

A whole worker run, as in test_dataset_matches_summary.py: the recorded Smashburger
SerpApi pages (real Google Maps review text, no network) go through the real
worker, normalizer and review store into Postgres. Then the real Q&A pipeline
answers a question, with the recording FakeModel in place of a live model.

The checks: the model receives exactly the ingested reviews (so none of the 12
skipped records, which test_dataset_matches_summary.py keeps out of the dataset),
with their real text intact and escaped; and another user gets none of it.
Skipped without TEST_DATABASE_URL.
"""

from html import escape

import pytest

from app.core.errors import NotFoundError
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.services.qa import answer_question
from app.worker import process_next_run
from tests.db.ingestion_worker.conftest import make_pipeline, serpapi
from tests.unit.qa.conftest import FakeModel

OTHER_PLACE_URL = "https://www.google.com/maps/place/Other/data=!4m2!3m1!1s0x2:0x2"


@pytest.fixture
def ingested(conn, target, queued_run):
    """Run the queued Smashburger run to the end; return its stored reviews."""
    open_source, _calls = serpapi()
    reviews = ReviewRepository(conn)
    process_next_run(make_pipeline(conn, open_source, reviews))
    stored = reviews.list_for_target(target["id"], target["owner_id"])
    assert len(stored) == 18  # 30 records, 12 rating-only skipped
    return stored


def ask(conn, model, owner_id, target_id, question="What do people like here?"):
    return answer_question(
        owner_id=owner_id,
        target_id=target_id,
        question=question,
        targets=TargetRepository(conn),
        reviews=ReviewRepository(conn),
        model=model,
    )


def sent_ids(request) -> list[str]:
    return [
        line.split('id="')[1].split('"')[0]
        for line in request.reviews_block.splitlines()
        if line.startswith("<review id=")
    ]


def test_model_receives_exactly_the_ingested_reviews(conn, target, ingested):
    model = FakeModel()
    ask(conn, model, target["owner_id"], target["id"])

    assert sent_ids(model.last) == [str(r["id"]) for r in ingested]
    assert '"Smashburger"' in model.last.system_prompt
    assert "Reviews supplied: 18" in model.last.system_prompt


def test_real_review_text_reaches_the_model_intact_and_escaped(conn, target, ingested):
    model = FakeModel()
    ask(conn, model, target["owner_id"], target["id"])

    block = model.last.reviews_block
    for review in ingested:
        assert escape(review["review_text"], quote=False) in block
        # Review text stays out of the system prompt.
        assert review["review_text"] not in model.last.system_prompt
    # However the text reads, the block keeps one open and close tag per review.
    assert block.count("</review>") == len(ingested)


def test_another_user_never_gets_the_ingested_reviews(
    conn, make_user, target, ingested
):
    other = make_user()
    other_target = TargetRepository(conn).create(
        other, "Other Place", "google_maps", OTHER_PLACE_URL
    )

    # Against the Smashburger target: not found, nothing sent.
    model = FakeModel()
    with pytest.raises(NotFoundError):
        ask(conn, model, other, target["id"])
    assert model.requests == []

    # Against their own (empty) target: no reviews, nothing sent.
    result = ask(conn, model, other, other_target["id"])
    assert result.status == "NO_REVIEWS"
    assert model.requests == []
