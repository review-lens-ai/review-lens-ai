"""S2-009, S2-010: POST /api/v1/targets/{id}/questions against real Postgres.

The real route, auth, repositories, tools and SQL run; only the model is the
recording FakeChatModel (get_model_factory is replaced in every test, so no test
can reach a live model even when backend/.env has an OpenRouter key).
Skipped without TEST_DATABASE_URL.
"""

from types import SimpleNamespace

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


class CountingFactory:
    def __init__(self, model):
        self.model, self.made = model, 0

    def __call__(self):
        self.made += 1
        return self.model


@pytest.fixture
def api(client, conn):
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def use_model(api):
    """use_model(model) makes the endpoint use `model`; returns the factory."""

    def _use(model):
        factory = model if callable(model) else CountingFactory(model)
        app.dependency_overrides[get_model_factory] = lambda: factory
        return factory

    return _use


@pytest.fixture
def make_target(conn):
    def _make(owner_id, reviews=REVIEWS) -> str:
        target = TargetRepository(conn).create(owner_id, "Joe's", "google_maps", URL)
        if reviews:
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
                    ReviewCreate(rating=r, review_text=t, mapping_version="serpapi@1")
                    for t, r in reviews
                ],
            )
        return str(target["id"])

    return _make


def ask(api, make_token, owner, target_id, question="What do people like?"):
    return api.post(
        f"/api/v1/targets/{target_id}/questions",
        json={"question": question},
        headers=auth(make_token(str(owner))),
    )


def test_a_question_gets_an_answer_from_the_targets_own_reviews(
    api, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner)
    model = FakeChatModel(
        call("search_reviews", keywords="crust"),
        submit("ANSWERED", "People love the crust.", cited=["R1"]),
    )
    use_model(model)

    response = ask(api, make_token, owner, target_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ANSWERED"
    assert [e["review_text"] for e in body["evidence"]] == ["The crust is perfect"]
    assert [s["tool"] for s in body["steps"]] == ["search_reviews"]
    assert body["context"]["target_id"] == target_id
    assert body["context"]["reviews_total"] == 2
    assert "Slow service at lunch" not in model.sent_text()  # not shown, not sent


def test_another_users_target_is_404_and_no_model_is_made(
    api, use_model, make_user, make_target, make_token
):
    target_id = make_target(make_user())
    factory = use_model(FakeChatModel())

    response = ask(api, make_token, make_user(), target_id)

    assert response.status_code == 404
    assert factory.made == 0


def test_a_target_without_reviews_answers_no_reviews_and_no_model_is_made(
    api, use_model, make_user, make_target, make_token
):
    owner = make_user()
    target_id = make_target(owner, reviews=[])
    factory = use_model(FakeChatModel())

    response = ask(api, make_token, owner, target_id)

    assert response.status_code == 200
    assert response.json()["status"] == "NO_REVIEWS"
    assert factory.made == 0


def test_a_blank_question_is_422_and_no_model_is_made(
    api, use_model, make_user, make_target, make_token
):
    owner = make_user()
    factory = use_model(FakeChatModel())

    response = ask(api, make_token, owner, make_target(owner), question="   ")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_QUESTION"
    assert factory.made == 0


def test_a_model_that_is_not_configured_is_503_without_provider_text(
    api, use_model, make_user, make_target, make_token
):
    owner = make_user()

    def not_configured():
        raise ModelUnavailableError("OPENROUTER_API_KEY and QA_MODEL must be set")

    use_model(not_configured)

    response = ask(api, make_token, owner, make_target(owner))

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "QA_UNAVAILABLE"
    assert "OPENROUTER" not in response.text


def test_the_real_factory_makes_the_model_only_when_called():
    settings = SimpleNamespace(openrouter_api_key=None, qa_model=None)

    make_model = get_model_factory(settings)  # nothing is made yet

    with pytest.raises(ModelUnavailableError):
        make_model()
