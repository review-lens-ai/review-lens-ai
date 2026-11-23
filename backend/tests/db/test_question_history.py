"""S3-006: the Q&A history is saved with the target (S3-BR-009) and restored in
order (S3-BR-010), through the real routes against real Postgres:

    POST   /api/v1/targets/{id}/questions   saves each 200 answer
    GET    /api/v1/targets/{id}/questions   the history, oldest first
    DELETE /api/v1/targets/{id}/questions   clears the Q&A only

Only the model is the recording FakeChatModel. Skipped without TEST_DATABASE_URL.
"""

import pytest

from app.api.questions import get_model_factory
from app.db import get_connection
from app.llm import ModelUnavailableError
from app.main import app
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from tests.conftest import auth
from tests.unit.qa.conftest import FakeChatModel, call, submit

URL = "https://www.google.com/maps/place/Joes+Pizza"
REVIEWS = [("The crust is perfect", 5), ("Slow service at lunch", 2)]


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def use_model(api):
    """use_model(*turns) scripts the model for the next questions."""

    def _use(*turns):
        model = FakeChatModel(*turns)
        app.dependency_overrides[get_model_factory] = lambda: lambda: model

    return _use


@pytest.fixture
def make_target(conn):
    def _make(owner_id, name="Joe's", reviews=REVIEWS) -> str:
        target = TargetRepository(conn).create(owner_id, name, "google_maps", URL)
        if reviews:
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
                    ReviewCreate(rating=r, review_text=t, mapping_version="serpapi@1")
                    for t, r in reviews
                ],
            )
        return str(target["id"])

    return _make


def answered(text: str) -> list:
    """Model turns for a real ANSWERED reply: a search returns R1 ("The crust is
    perfect"), then the answer cites it. Without a citation a tool returned, the
    app turns ANSWERED into INSUFFICIENT_EVIDENCE."""
    return [
        call("search_reviews", keywords="crust"),
        submit("ANSWERED", text, cited=["R1"]),
    ]


def _url(target_id) -> str:
    return f"/api/v1/targets/{target_id}/questions"


def ask(api, make_token, owner, target_id, question="What do people like?"):
    return api.post(
        _url(target_id),
        json={"question": question},
        headers=auth(make_token(str(owner))),
    )


def history(api, make_token, owner, target_id):
    return api.get(_url(target_id), headers=auth(make_token(str(owner))))


def clear(api, make_token, owner, target_id):
    return api.delete(_url(target_id), headers=auth(make_token(str(owner))))


def saved_count(conn, target_id) -> int:
    return conn.execute(
        "select count(*) as n from public.questions where target_id = %s",
        (target_id,),
    ).fetchone()["n"]


def test_history_returns_questions_oldest_first(
    api, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner)
    questions = ["First?", "Second?", "Third?"]
    use_model(*(turn for q in questions for turn in answered(f"Answer to {q}")))
    for question in questions:
        assert ask(api, make_token, owner, target_id, question).status_code == 200

    response = history(api, make_token, owner, target_id)

    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["question"] for item in items] == questions
    assert [item["response"]["answer"] for item in items] == [
        f"Answer to {q}" for q in questions
    ]


def test_history_response_matches_the_answer_the_user_saw(
    api, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner)
    use_model(*answered("People love the crust."))
    answer = ask(api, make_token, owner, target_id, "  Crust?  ").json()

    item = history(api, make_token, owner, target_id).json()["items"][0]

    assert item["question"] == "Crust?"  # saved as it was answered, trimmed
    assert item["response"] == answer
    assert [e["review_text"] for e in item["response"]["evidence"]] == [
        "The crust is perfect"
    ]


def test_evidence_is_saved_as_review_ids_only(
    api, conn, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner)
    use_model(*answered("People love the crust."))
    cited = ask(api, make_token, owner, target_id).json()["evidence"][0]

    stored = conn.execute(
        "select response from public.questions where target_id = %s", (target_id,)
    ).fetchone()["response"]

    assert stored["evidence"] == [{"id": cited["id"]}]
    assert "The crust is perfect" not in str(stored)


def test_refusal_is_saved_in_history(
    api, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner)
    use_model(
        submit(
            "OUT_OF_SCOPE",
            "I only answer about these reviews.",
            refusal_reason="GENERAL_KNOWLEDGE",
        )
    )
    ask(api, make_token, owner, target_id, "What is the capital of France?")

    items = history(api, make_token, owner, target_id).json()["items"]

    assert [item["response"]["status"] for item in items] == ["OUT_OF_SCOPE"]
    assert items[0]["response"]["refusal_reason"] == "GENERAL_KNOWLEDGE"


def test_no_reviews_answer_is_saved_in_history(
    api, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner, reviews=[])
    use_model()

    ask(api, make_token, owner, target_id)

    items = history(api, make_token, owner, target_id).json()["items"]
    assert [item["response"]["status"] for item in items] == ["NO_REVIEWS"]


def test_model_failure_saves_nothing(api, conn, make_user, make_target, make_token):
    owner = make_user()
    target_id = make_target(owner)

    def not_configured():
        raise ModelUnavailableError("not configured")

    app.dependency_overrides[get_model_factory] = lambda: not_configured

    assert ask(api, make_token, owner, target_id).status_code == 503
    assert saved_count(conn, target_id) == 0


def test_blank_question_saves_nothing(
    api, conn, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner)
    use_model()

    assert ask(api, make_token, owner, target_id, "   ").status_code == 422
    assert saved_count(conn, target_id) == 0


def test_user_b_cannot_read_user_a_history(
    api, use_model, make_user, make_target, make_token
):
    user_a, user_b = make_user(), make_user()
    target_id = make_target(user_a)
    use_model(*answered("A's answer."))
    ask(api, make_token, user_a, target_id)
    assert len(history(api, make_token, user_a, target_id).json()["items"]) == 1

    response = history(api, make_token, user_b, target_id)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "A's answer." not in response.text


def test_user_b_cannot_clear_user_a_history(
    api, conn, use_model, make_user, make_target, make_token
):
    user_a, user_b = make_user(), make_user()
    target_id = make_target(user_a)
    use_model(*answered("A's answer."))
    ask(api, make_token, user_a, target_id)

    response = clear(api, make_token, user_b, target_id)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert saved_count(conn, target_id) == 1


def test_user_b_question_to_user_a_target_saves_nothing(
    api, conn, use_model, make_user, make_target, make_token
):
    user_a, user_b = make_user(), make_user()
    target_id = make_target(user_a)
    use_model(submit("ANSWERED", "Should never run."))

    assert ask(api, make_token, user_b, target_id).status_code == 404
    assert saved_count(conn, target_id) == 0


def test_clearing_history_keeps_target_reviews_and_runs(
    api, conn, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner)
    use_model(*answered("One."), *answered("Two."))
    ask(api, make_token, owner, target_id)
    ask(api, make_token, owner, target_id)
    headers = auth(make_token(str(owner)))
    summary_before = api.get(f"/api/v1/targets/{target_id}/summary", headers=headers)
    runs_before = conn.execute(
        "select id, status, reviews_ingested from public.ingestion_runs "
        "where target_id = %s",
        (target_id,),
    ).fetchall()

    response = clear(api, make_token, owner, target_id)

    assert response.status_code == 204
    assert history(api, make_token, owner, target_id).json() == {"items": []}
    assert api.get(f"/api/v1/targets/{target_id}", headers=headers).status_code == 200
    summary_after = api.get(f"/api/v1/targets/{target_id}/summary", headers=headers)
    assert summary_after.json() == summary_before.json()
    assert summary_after.json()["review_count"] == len(REVIEWS)
    runs_after = conn.execute(
        "select id, status, reviews_ingested from public.ingestion_runs "
        "where target_id = %s",
        (target_id,),
    ).fetchall()
    assert runs_after == runs_before


def test_two_targets_keep_separate_histories(
    api, conn, use_model, make_user, make_target, make_token
):
    owner = make_user()
    first = make_target(owner, name="First place")
    second = make_target(owner, name="Second place")
    use_model(*answered("About first."), *answered("About second."))
    ask(api, make_token, owner, first, "First?")
    ask(api, make_token, owner, second, "Second?")

    assert [
        i["question"] for i in history(api, make_token, owner, first).json()["items"]
    ] == ["First?"]
    assert [
        i["question"] for i in history(api, make_token, owner, second).json()["items"]
    ] == ["Second?"]

    assert clear(api, make_token, owner, first).status_code == 204

    assert saved_count(conn, first) == 0
    assert [
        i["question"] for i in history(api, make_token, owner, second).json()["items"]
    ] == ["Second?"]


def test_evidence_only_comes_from_the_same_target(
    api, conn, use_model, make_user, make_target, make_token
):
    """A saved id that points at another target's review is left out on read."""
    owner = make_user()
    target_id = make_target(owner)
    other_id = make_target(make_user(), name="Other", reviews=[("Other secret", 1)])
    other_review = conn.execute(
        "select id from public.reviews where target_id = %s", (other_id,)
    ).fetchone()["id"]
    use_model(*answered("Crust."))
    ask(api, make_token, owner, target_id)
    conn.execute(
        "update public.questions "
        "set response = jsonb_set(response, '{evidence}', %s::jsonb) "
        "where target_id = %s",
        (f'[{{"id": "{other_review}"}}]', target_id),
    )

    response = history(api, make_token, owner, target_id)

    assert response.json()["items"][0]["response"]["evidence"] == []
    assert "Other secret" not in response.text


def test_clearing_an_empty_history_returns_204(
    api, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner)

    assert clear(api, make_token, owner, target_id).status_code == 204
