"""S2-003: Q&A context isolation against a real Postgres.

The same checks as tests/unit/qa/test_orchestrator.py, but with the real
TargetRepository and ReviewRepository, so the SQL ownership filters are what is
being tested. The model is the recording FakeModel; nothing calls a live model.

User A owns TARGET A (one review holds LEAK_PHRASE), a second target A2 and an
empty target. User B owns target B (one review holds B_PHRASE). Skipped without
TEST_DATABASE_URL.

Demo (Sprint 2 step 7 and section 3.4): the two tests marked "Demo" print the
request the model would have received. Run them with -s to show it:

    TEST_DATABASE_URL=... uv run pytest tests/db/test_qa_context_isolation.py \\
        -k demo -s -v
"""

import pytest

from app.core.errors import NotFoundError
from app.repositories.ingestion_runs import IngestionRunRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.qa import AnswerStatus
from app.schemas.reviews import ReviewCreate
from app.services.qa import answer_question
from tests.unit.qa.conftest import LEAK_PHRASE, FakeModel, answered

PLACE_URL = "https://www.google.com/maps/place/Test/data=!4m2!3m1!1s0x1:0x{}"

# Distinctive to user B's dataset; appears nowhere in user A's.
B_PHRASE = "MANGO-WAFFLE-9"


def add_target(conn, owner_id, name, n, texts):
    """A target for `owner_id` with one finished run that saved `texts`."""
    target = TargetRepository(conn).create(
        owner_id, name, "google_maps", PLACE_URL.format(n)
    )
    if texts:
        runs = IngestionRunRepository(conn)
        run = runs.mark_running(runs.create_run(target["id"], owner_id)["id"])
        ReviewRepository(conn).bulk_insert(
            run["id"],
            target["id"],
            owner_id,
            [
                ReviewCreate(rating=4, review_text=text, mapping_version="serpapi@1")
                for text in texts
            ],
        )
        runs.mark_succeeded(run["id"], reviews_ingested=len(texts))
    return target["id"]


@pytest.fixture
def data(conn, make_user):
    user_a, user_b = make_user(), make_user()
    return {
        "conn": conn,
        "user_a": user_a,
        "user_b": user_b,
        "target_a": add_target(
            conn,
            user_a,
            "A's place",
            1,
            [f"The {LEAK_PHRASE} special was great.", "Slow service."],
        ),
        "target_a2": add_target(conn, user_a, "A's cafe", 2, ["Good espresso."]),
        "target_empty": add_target(conn, user_a, "A's new place", 3, []),
        "target_b": add_target(
            conn,
            user_b,
            "B's diner",
            4,
            ["Big portions.", f"The {B_PHRASE} brunch is unmatched."],
        ),
    }


def ask(data, model, owner, target, question="What do people say?"):
    conn = data["conn"]
    return answer_question(
        owner_id=data[owner],
        target_id=data[target],
        question=question,
        targets=TargetRepository(conn),
        reviews=ReviewRepository(conn),
        model=model,
    )


def stored_ids(conn, target_id) -> set[str]:
    rows = conn.execute(
        "select id from public.reviews where target_id = %s", (target_id,)
    ).fetchall()
    return {str(r["id"]) for r in rows}


def sent_ids(request) -> set[str]:
    return {
        line.split('id="')[1].split('"')[0]
        for line in request.reviews_block.splitlines()
        if line.startswith("<review id=")
    }


def show_request(title, request):
    """Print a captured ModelRequest; visible only when pytest runs with -s."""
    print(f"\n===== {title} =====")
    print(f"--- question ---\n{request.question}")
    print(f"--- system prompt ---\n{request.system_prompt}")
    print(f"--- reviews sent to the model ---\n{request.reviews_block}")


def test_demo_user_b_phrase_never_reaches_user_a_context(data):
    """Demo: user A asks about user B's distinctive phrase. The captured request
    holds only user A's active target reviews; the phrase is nowhere in it."""
    conn = data["conn"]
    model = FakeModel()
    ask(
        data,
        model,
        "user_a",
        "target_a",
        question=f"What do reviewers say about the {B_PHRASE} brunch?",
    )

    request = model.last
    show_request("User A's request (active target: A's place)", request)
    assert sent_ids(request) == stored_ids(conn, data["target_a"])
    assert sent_ids(request).isdisjoint(stored_ids(conn, data["target_b"]))
    # The phrase reaches the model only inside A's own question.
    assert B_PHRASE not in request.reviews_block
    assert B_PHRASE not in request.system_prompt


def test_demo_active_target_determines_the_context(data):
    """Demo: user A switches from A's place to A's cafe. The second request holds
    only the cafe's reviews; nothing from the first target is carried over."""
    conn = data["conn"]
    model = FakeModel()
    ask(data, model, "user_a", "target_a")
    ask(data, model, "user_a", "target_a2")

    first, second = model.requests
    show_request("Before the switch (active target: A's place)", first)
    show_request("After the switch (active target: A's cafe)", second)
    assert sent_ids(first) == stored_ids(conn, data["target_a"])
    assert sent_ids(second) == stored_ids(conn, data["target_a2"])
    assert sent_ids(second).isdisjoint(stored_ids(conn, data["target_a"]))
    assert LEAK_PHRASE not in second.reviews_block
    assert "A's place" not in second.system_prompt


def test_context_holds_exactly_the_active_targets_stored_reviews(data):
    model = FakeModel()
    ask(data, model, "user_a", "target_a")

    assert sent_ids(model.last) == stored_ids(data["conn"], data["target_a"])


def test_user_b_context_never_contains_user_a_reviews(data):
    model = FakeModel()
    ask(
        data,
        model,
        "user_b",
        "target_b",
        question=f"What about the {LEAK_PHRASE} special?",
    )

    request = model.last
    assert sent_ids(request) == stored_ids(data["conn"], data["target_b"])
    assert LEAK_PHRASE not in request.reviews_block
    assert LEAK_PHRASE not in request.system_prompt


def test_user_b_cannot_ask_about_user_a_target(data):
    model = FakeModel()
    with pytest.raises(NotFoundError):
        ask(data, model, "user_b", "target_a")

    assert model.requests == []


def test_user_b_cannot_get_user_a_review_through_a_citation(data):
    a_review = next(iter(stored_ids(data["conn"], data["target_a"])))
    result = ask(data, FakeModel(answered(a_review)), "user_b", "target_b")

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.evidence == []


def test_target_without_reviews_does_not_call_the_model(data):
    model = FakeModel()
    result = ask(data, model, "user_a", "target_empty")

    assert result.status is AnswerStatus.NO_REVIEWS
    assert model.requests == []


def test_answer_evidence_is_the_stored_review(data):
    conn = data["conn"]
    review_id = next(iter(stored_ids(conn, data["target_a2"])))
    result = ask(data, FakeModel(answered(review_id)), "user_a", "target_a2")

    assert result.status is AnswerStatus.ANSWERED
    assert [str(e.id) for e in result.evidence] == [review_id]
    assert result.evidence[0].review_text == "Good espresso."
