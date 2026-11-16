"""S2-018, S2-019: switching targets changes everything the model is told, through
the real HTTP endpoint POST /api/v1/targets/{id}/questions (S2-BR-022 to S2-BR-024).

tests/db/test_qa_context_isolation.py proves this through the orchestrator directly
(test_demo_active_target_determines_the_context). Here the whole request runs: the
token picks the user, the real route, repositories, tools and SQL run on a real
database, and only the model is a recording FakeChatModel, a new one per request.

Nothing in Q&A caches review context today (app/services/qa/context.py: "fresh every
question"). These tests are the guard for later: a cache keyed on the question, or
context kept from the previous target, makes them fail.

User A owns two targets with clearly different reviews and numbers: "Joe's Pizza"
(every review mentions crust) and "Harbor Mall Cafe" (every review mentions parking).
Skipped without TEST_DATABASE_URL.
"""

import pytest
from langchain_core.messages import ToolMessage

from app.api.questions import get_model_factory
from app.core.config import get_settings
from app.db import get_connection
from app.main import app
from app.repositories.ingestion_runs import IngestionRunRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewCreate
from tests.conftest import auth
from tests.db.test_qa_context_isolation import (
    PLACE_URL,
    sent_texts,
    stored_texts,
    system_prompt,
)
from tests.unit.qa.conftest import FakeChatModel, call, submit

READ_ALL = {"order": "newest", "limit": 10}

PIZZA = {
    "name": "Joe's Pizza",
    "word": "crust",
    "reviews": [
        ("The crust is perfectly crisp.", 5),
        ("Best crust in town.", 5),
        ("Crust was a bit burnt this time.", 4),
    ],
}
CAFE = {
    "name": "Harbor Mall Cafe",
    "word": "parking",
    "reviews": [
        ("Parking is impossible on weekends.", 2),
        ("No parking nearby, we left.", 1),
    ],
}


@pytest.fixture
def api(client, conn, monkeypatch):
    # The scripts below stay within six tool calls in tools mode; pin both so a
    # local backend/.env cannot change how the question runs.
    monkeypatch.setenv("QA_MODE", "tools")
    monkeypatch.setenv("QA_MAX_TOOL_CALLS", "6")
    get_settings.cache_clear()
    app.dependency_overrides[get_connection] = lambda: conn
    return client


class ModelsInOrder:
    """A model factory that hands out the next scripted model on each call."""

    def __init__(self, *models):
        self.models, self.made = list(models), 0

    def __call__(self):
        self.made += 1
        return self.models.pop(0)


def add_rated_target(conn, owner_id, spec, n):
    """Like add_target in the isolation test, but with a rating per review, so the
    two targets' summaries differ."""
    target = TargetRepository(conn).create(
        owner_id, spec["name"], "google_maps", PLACE_URL.format(n)
    )
    runs = IngestionRunRepository(conn)
    run = runs.mark_running(runs.create_run(target["id"], owner_id)["id"])
    ReviewRepository(conn).bulk_insert(
        run["id"],
        target["id"],
        owner_id,
        [
            ReviewCreate(rating=rating, review_text=text, mapping_version="serpapi@1")
            for text, rating in spec["reviews"]
        ],
    )
    runs.mark_succeeded(run["id"], reviews_ingested=len(spec["reviews"]))
    return target["id"]


@pytest.fixture
def data(conn, make_user):
    owner = make_user()
    return {
        "owner": owner,
        "pizza": {**PIZZA, "id": add_rated_target(conn, owner, PIZZA, 1)},
        "cafe": {**CAFE, "id": add_rated_target(conn, owner, CAFE, 2)},
    }


def ask(api, make_token, owner_id, target_id, question):
    return api.post(
        f"/api/v1/targets/{target_id}/questions",
        json={"question": question},
        headers=auth(make_token(str(owner_id))),
    )


def reads_target(target, other) -> FakeChatModel:
    """A model that looks for the other target's word, reads the active target's
    reviews and cites every one of them."""
    labels = [f"R{i}" for i in range(1, len(target["reviews"]) + 1)]
    return FakeChatModel(
        call("search_reviews", keywords=other["word"]),
        call("search_reviews", **READ_ALL),
        submit("ANSWERED", "Here is what these reviews say.", cited=labels),
    )


def tool_results(model: FakeChatModel) -> list[str]:
    return [m.content for m in model.last["messages"] if isinstance(m, ToolMessage)]


def review_ids(conn, target_id) -> set[str]:
    rows = conn.execute(
        "select id from public.reviews where target_id = %s", (target_id,)
    ).fetchall()
    return {str(r["id"]) for r in rows}


def briefing_line(target) -> str:
    ratings = [rating for _, rating in target["reviews"]]
    average = sum(ratings) / len(ratings)
    return f"Saved reviews: {len(ratings)}. Average rating: {average:.2f} out of 5."


def assert_told_only_about(conn, model, response, target, other):
    """Everything the model was told, and everything returned, is about `target`."""
    assert response.status_code == 200
    body = response.json()

    # S2-BR-023: the dataset is the active target's stored reviews, exactly.
    assert sent_texts(model) == stored_texts(conn, target["id"])
    assert body["context"]["target_id"] == str(target["id"])
    assert {e["id"] for e in body["evidence"]} == review_ids(conn, target["id"])

    # S2-BR-024: nothing of the other target remains: not its reviews (the search
    # for its word found nothing), not its name, not its numbers.
    assert tool_results(model)[0].startswith("No reviews match.")
    prompt = system_prompt(model)
    seen = "\n".join(tool_results(model) + [prompt]).lower()
    assert other["word"] not in seen
    assert f'Active target: "{target["name"]}"' in prompt
    assert other["name"] not in prompt

    # The summary is the active target's.
    assert briefing_line(target) in prompt
    assert briefing_line(other) not in prompt
    assert body["context"]["reviews_total"] == len(target["reviews"])


def test_switching_targets_changes_everything_the_model_is_told(
    api, data, conn, make_token
):
    """User A asks on Joe's Pizza, switches to Harbor Mall Cafe, then back."""
    pizza, cafe = data["pizza"], data["cafe"]
    first, second, third = (
        reads_target(pizza, cafe),
        reads_target(cafe, pizza),
        reads_target(pizza, cafe),
    )
    factory = ModelsInOrder(first, second, third)
    app.dependency_overrides[get_model_factory] = lambda: factory

    on_pizza = ask(api, make_token, data["owner"], pizza["id"], "What do people say?")
    on_cafe = ask(api, make_token, data["owner"], cafe["id"], "What do people say?")
    back = ask(api, make_token, data["owner"], pizza["id"], "What do people say?")

    assert factory.made == 3 and factory.models == []
    assert_told_only_about(conn, first, on_pizza, pizza, cafe)
    assert_told_only_about(conn, second, on_cafe, cafe, pizza)
    # Back on the pizza place, R1 to R3 are its reviews again: nothing carried over
    # from the cafe.
    assert_told_only_about(conn, third, back, pizza, cafe)


def test_the_same_question_on_two_targets_is_not_reused(api, data, conn, make_token):
    """S2-019: an answer is never looked up by question text alone. The identical
    question on a second target runs again, on that target's reviews."""
    pizza, cafe = data["pizza"], data["cafe"]
    question = "What do people complain about?"
    first, second = reads_target(pizza, cafe), reads_target(cafe, pizza)
    factory = ModelsInOrder(first, second)
    app.dependency_overrides[get_model_factory] = lambda: factory

    on_pizza = ask(api, make_token, data["owner"], pizza["id"], question)
    on_cafe = ask(api, make_token, data["owner"], cafe["id"], question)

    # Both questions reached a model: the second answer was not reused.
    assert factory.made == 2
    assert first.calls and second.calls
    assert_told_only_about(conn, first, on_pizza, pizza, cafe)
    assert_told_only_about(conn, second, on_cafe, cafe, pizza)
