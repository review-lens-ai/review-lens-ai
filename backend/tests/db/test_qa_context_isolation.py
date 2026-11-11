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


# --- Demo output ------------------------------------------------------------------
# The two demo tests print their whole argument, visible with pytest -s: what is
# in the database, what was asked, the exact request the model received (captured
# by FakeModel at the model boundary), who owns each review that was sent, and
# each check. The asserts below the printout are what make the test pass or fail.


def owner_label(data, owner_id) -> str:
    return "User A" if owner_id == data["user_a"] else "User B"


def review_owners(conn) -> dict[str, tuple]:
    """Every stored review id -> (review text, target name, owner id)."""
    rows = conn.execute(
        """
        select r.id, r.review_text, t.name, t.owner_id
          from public.reviews r
          join public.analysis_targets t on t.id = r.target_id
        """
    ).fetchall()
    return {str(r["id"]): (r["review_text"], r["name"], r["owner_id"]) for r in rows}


def print_dataset(data, target_key, marker=None, label=""):
    conn = data["conn"]
    target = conn.execute(
        "select name, owner_id from public.analysis_targets where id = %s",
        (data[target_key],),
    ).fetchone()
    texts = sorted(
        r["review_text"]
        for r in conn.execute(
            "select review_text from public.reviews where target_id = %s",
            (data[target_key],),
        ).fetchall()
    )
    print(
        f'  {owner_label(data, target["owner_id"])} owns "{target["name"]}" '
        f"({len(texts)} review{'' if len(texts) == 1 else 's'})"
        + (f"   <-- {label}" if label else "")
    )
    for text in texts:
        note = (
            f"   <-- distinctive phrase {marker}" if marker and marker in text else ""
        )
        print(f"    - {text}{note}")


def print_request(data, request):
    """The captured ModelRequest, then the owner of each review in it."""
    prompt_lines = request.system_prompt.splitlines()
    print("  Question sent:")
    print(f"    {request.question}")
    print("  System prompt (dataset lines; rules omitted here):")
    for line in prompt_lines:
        if line.startswith(("- Target:", "- Review platform:", "- Reviews supplied:")):
            print(f"    {line}")
    print("  Review block, exactly as sent:")
    for line in request.reviews_block.splitlines():
        print(f"    {line}")
    owners = review_owners(data["conn"])
    print("  Who owns each review that was sent (looked up in the database):")
    for review_id in sorted(sent_ids(request)):
        text, target_name, owner_id = owners[review_id]
        print(f'    - "{text}"  ->  {owner_label(data, owner_id)}, "{target_name}"')


def print_checks(checks):
    for label, ok in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def heading(text):
    print(f"\n{'=' * 72}\n {text}\n{'=' * 72}")


def step(text):
    print(f"\n{text}")


def test_demo_user_b_phrase_never_reaches_user_a_context(data):
    """Demo (step 7): user A asks about user B's distinctive phrase. The model
    receives only user A's active target reviews; the phrase is nowhere in them."""
    conn = data["conn"]
    question = f"What do reviewers say about the {B_PHRASE} brunch?"
    model = FakeModel()
    ask(data, model, "user_a", "target_a", question=question)
    request = model.last

    a_ids = stored_ids(conn, data["target_a"])
    a_other_ids = stored_ids(conn, data["target_a2"])
    b_ids = stored_ids(conn, data["target_b"])
    checks = [
        (
            f"The model received exactly User A's {len(a_ids)} reviews for "
            '"A\'s place" (the active target)',
            sent_ids(request) == a_ids,
        ),
        (
            "The model received none of User A's other target (\"A's cafe\")",
            sent_ids(request).isdisjoint(a_other_ids),
        ),
        (
            "The model received none of User B's reviews",
            sent_ids(request).isdisjoint(b_ids),
        ),
        (
            f'"{B_PHRASE}" is not in the reviews or the system prompt',
            B_PHRASE not in request.reviews_block
            and B_PHRASE not in request.system_prompt,
        ),
    ]

    heading("DEMO: User B's review data never enters User A's AI context")
    step("1. What is in the database (two real users)")
    print_dataset(data, "target_a", label="active target")
    print_dataset(data, "target_a2", label="User A's other target")
    print_dataset(data, "target_b", marker=B_PHRASE)
    step("2. User A, on their active target \"A's place\", asks for User B's phrase")
    print(f"    {question}")
    step("3. What ReviewLens sent to the model")
    print("  (captured by FakeModel, which sits exactly where the LLM would be)")
    print_request(data, request)
    step("4. Checks")
    print_checks(checks)

    assert sent_ids(request) == a_ids
    assert sent_ids(request).isdisjoint(a_other_ids)
    assert sent_ids(request).isdisjoint(b_ids)
    # The phrase reaches the model only inside A's own question.
    assert B_PHRASE not in request.reviews_block
    assert B_PHRASE not in request.system_prompt


def test_demo_active_target_determines_the_context(data):
    """Demo (step 6 evidence): user A switches from A's place to A's cafe. The
    second request holds only the cafe's reviews; nothing carries over."""
    conn = data["conn"]
    model = FakeModel()
    ask(data, model, "user_a", "target_a")
    ask(data, model, "user_a", "target_a2")
    first, second = model.requests

    place_ids = stored_ids(conn, data["target_a"])
    cafe_ids = stored_ids(conn, data["target_a2"])
    checks = [
        (
            'Before the switch: exactly "A\'s place" reviews were sent',
            sent_ids(first) == place_ids,
        ),
        (
            'After the switch: exactly "A\'s cafe" reviews were sent',
            sent_ids(second) == cafe_ids,
        ),
        (
            'After the switch: none of "A\'s place" reviews were sent',
            sent_ids(second).isdisjoint(place_ids),
        ),
        (
            'After the switch: the system prompt no longer names "A\'s place"',
            "A's place" not in second.system_prompt,
        ),
    ]

    heading("DEMO: the active target decides the AI context (no stale reviews)")
    step("1. What is in the database (User A's two targets)")
    print_dataset(data, "target_a", marker=LEAK_PHRASE)
    print_dataset(data, "target_a2")
    step('2. User A asks on "A\'s place". What ReviewLens sent to the model:')
    print_request(data, first)
    step('3. User A switches to "A\'s cafe" and asks again. What was sent:')
    print_request(data, second)
    step("4. Checks")
    print_checks(checks)

    assert sent_ids(first) == place_ids
    assert sent_ids(second) == cafe_ids
    assert sent_ids(second).isdisjoint(place_ids)
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
