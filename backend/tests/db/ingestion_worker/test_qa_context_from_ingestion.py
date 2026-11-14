"""S2-010: Q&A context built from reviews the real worker ingested.

A whole worker run, as in test_dataset_matches_summary.py: the recorded Smashburger
SerpApi pages (real Google Maps review text, no network) go through the real
worker, normalizer and review store into Postgres. Then the real Q&A pipeline
answers a question, with the recording FakeChatModel in place of a live model.

The checks: in full mode the model receives exactly the 18 ingested reviews (so
none of the 12 skipped records), each quoted so it cannot pass as an instruction;
in tools mode a real search returns only ingested text; another user gets none of
it. Skipped without TEST_DATABASE_URL.
"""

import json
import re

import pytest
from langchain_core.messages import ToolMessage

from app.core.errors import NotFoundError
from app.repositories.review_search import ReviewSearchRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.services.qa import NoReviewsError, QAConfig, answer_question
from app.services.qa.tools.guard import FLAG
from app.worker import process_next_run
from tests.db.ingestion_worker.conftest import make_pipeline, serpapi
from tests.unit.qa.conftest import FakeChatModel, call, submit

OTHER_PLACE_URL = "https://www.google.com/maps/place/Other/data=!4m2!3m1!1s0x2:0x2"
_REVIEW_LINE = re.compile(r"^R\d+ \| ")


@pytest.fixture
def ingested(conn, target, queued_run):
    """Run the queued Smashburger run to the end; return its stored reviews."""
    open_source, _calls = serpapi()
    reviews = ReviewRepository(conn)
    process_next_run(make_pipeline(conn, open_source, reviews))
    stored = reviews.list_for_target(target["id"], target["owner_id"])
    assert len(stored) == 18  # 30 records, 12 rating-only skipped
    return stored


def ask(conn, model, owner_id, target_id, *, config=None):
    return answer_question(
        owner_id=owner_id,
        target_id=target_id,
        question="What do people like here?",
        targets=TargetRepository(conn),
        reviews=ReviewRepository(conn),
        search=ReviewSearchRepository(conn),
        make_model=lambda: model,
        config=config,
    )


def quoted_texts(content: str) -> list[str]:
    """The review texts on "R1 | detail | \"text\"" lines, decoded."""
    texts = []
    for line in content.splitlines():
        if _REVIEW_LINE.match(line):
            texts.append(json.loads(line.split(" | ", 2)[2].removeprefix(f"{FLAG} ")))
    return texts


def test_full_mode_sends_exactly_the_ingested_reviews(conn, target, ingested):
    model = FakeChatModel(submit("ANSWERED", "People like the burgers.", cited=["R1"]))
    result = ask(
        conn, model, target["owner_id"], target["id"], config=QAConfig(mode="full")
    )

    system, human = model.calls[0]["messages"]
    sent = quoted_texts(human.content)
    assert len(sent) == 18
    for review, text in zip(ingested, sent, strict=True):
        # Newest first, like the list; a review longer than the cap is shortened.
        assert review["review_text"].startswith(text.removesuffix("…"))
        assert review["review_text"] not in system.content
    assert '"Smashburger"' in system.content
    assert "Saved reviews: 18." in system.content
    assert result.evidence[0].review_text == ingested[0]["review_text"]


def test_a_real_search_returns_only_ingested_text(conn, target, ingested):
    model = FakeChatModel(
        call("search_reviews", order="newest", limit=10),
        submit("ANSWERED", "People like the burgers.", cited=["R1"]),
    )
    ask(conn, model, target["owner_id"], target["id"])

    [tool_result] = [
        m.content for m in model.last["messages"] if isinstance(m, ToolMessage)
    ]
    sent = quoted_texts(tool_result)
    assert len(sent) == 10
    stored = [r["review_text"] for r in ingested]
    for text in sent:
        assert any(full.startswith(text.removesuffix("…")) for full in stored)
    assert tool_result.startswith("10 of 18 matching reviews:")


def test_another_user_never_gets_the_ingested_reviews(
    conn, make_user, target, ingested
):
    other = make_user()
    other_target = TargetRepository(conn).create(
        other, "Other Place", "google_maps", OTHER_PLACE_URL
    )
    model = FakeChatModel()

    # Against the Smashburger target: not found, nothing sent.
    with pytest.raises(NotFoundError):
        ask(conn, model, other, target["id"])
    # Against their own (empty) target: no reviews, nothing sent.
    with pytest.raises(NoReviewsError):
        ask(conn, model, other, other_target["id"])
    assert model.calls == []
