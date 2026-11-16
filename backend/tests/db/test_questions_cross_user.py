"""S2-016: no user's reviews reach another user's AI context, through the real HTTP
endpoint POST /api/v1/targets/{id}/questions (S2-BR-022, S2-BR-026, S2-BR-030).

tests/db/test_qa_context_isolation.py proves this through the orchestrator directly,
and tests/db/test_questions_endpoint.py covers the route's 404 for another user's
target. Here the whole request runs: the token picks the user, the real route,
repositories, tools and SQL run on a real database, and only the model is the
recording FakeChatModel, so the checks look at what reached the model boundary and
what came back in the response body, not at model prose (S2-BR-029).

User A owns "A's place" (one review holds LEAK_PHRASE). User B owns "B's diner"
(one review holds B_PHRASE). Each user asks on their own target a question built to
pull out the other user's phrase, and the model is scripted to try every kind of
tool that could leak it: search by the phrase and by its words, a count, and the
most-used words. Skipped without TEST_DATABASE_URL.
"""

import pytest
from langchain_core.messages import HumanMessage, ToolMessage

from app.api.questions import get_model_factory
from app.core.config import get_settings
from app.db import get_connection
from app.main import app
from tests.conftest import auth
from tests.db.test_qa_context_isolation import (
    B_PHRASE,
    add_target,
    sent_texts,
    stored_texts,
)
from tests.unit.qa.conftest import LEAK_PHRASE, FakeChatModel, call, submit

READ_ALL = {"order": "newest", "limit": 10}


@pytest.fixture
def api(client, conn, monkeypatch):
    # The scripts below use exactly six tool calls in tools mode; pin both so a
    # local backend/.env cannot change how the question runs.
    monkeypatch.setenv("QA_MODE", "tools")
    monkeypatch.setenv("QA_MAX_TOOL_CALLS", "6")
    get_settings.cache_clear()
    app.dependency_overrides[get_connection] = lambda: conn
    return client


@pytest.fixture
def use_model(api):
    def _use(model):
        app.dependency_overrides[get_model_factory] = lambda: lambda: model

    return _use


@pytest.fixture
def data(conn, make_user):
    user_a, user_b = make_user(), make_user()
    return {
        "user_a": user_a,
        "user_b": user_b,
        "target_a": add_target(
            conn,
            user_a,
            "A's place",
            1,
            [f"The {LEAK_PHRASE} special was great.", "Slow service."],
        ),
        "target_b": add_target(
            conn,
            user_b,
            "B's diner",
            2,
            ["Big portions.", f"The {B_PHRASE} brunch is unmatched."],
        ),
    }


def ask(api, make_token, owner_id, target_id, question):
    return api.post(
        f"/api/v1/targets/{target_id}/questions",
        json={"question": question},
        headers=auth(make_token(str(owner_id))),
    )


def leak_attempt(phrase: str, word_1: str, word_2: str) -> FakeChatModel:
    """A model that tries every tool that could reveal `phrase`, then reads the
    active target's reviews and cites the first two."""
    return FakeChatModel(
        call("search_reviews", keywords=phrase),
        call("search_reviews", keywords=word_1),
        call("search_reviews", keywords=word_2),
        call("review_stats", keywords=word_1),
        call("top_terms"),
        call("search_reviews", **READ_ALL),
        submit("ANSWERED", "Here is what these reviews say.", cited=["R1", "R2"]),
    )


def tool_results(model: FakeChatModel) -> list[str]:
    """Each tool result sent back to the model, in the order the tools ran."""
    return [m.content for m in model.last["messages"] if isinstance(m, ToolMessage)]


def system_prompt(model: FakeChatModel) -> str:
    return model.calls[0]["messages"][0].content


def review_ids(conn, target_id) -> set[str]:
    rows = conn.execute(
        "select id from public.reviews where target_id = %s", (target_id,)
    ).fetchall()
    return {str(r["id"]) for r in rows}


def messages_containing(model: FakeChatModel, text: str) -> list:
    return [
        m
        for c in model.calls
        for m in c["messages"]
        if text.lower() in str(m.content).lower()
    ]


def test_user_b_cannot_pull_user_a_phrase_through_the_endpoint(
    api, use_model, data, conn, make_token
):
    question = f"What do reviewers say about the {LEAK_PHRASE} special?"
    model = leak_attempt(LEAK_PHRASE, "zebra", "pasta")
    use_model(model)

    response = ask(api, make_token, data["user_b"], data["target_b"], question)

    assert response.status_code == 200
    body = response.json()
    # The token and the URL chose B's target, and only B's.
    assert body["context"]["target_id"] == str(data["target_b"])

    # The model received exactly B's reviews: none of A's text reached it.
    assert sent_texts(model) == stored_texts(conn, data["target_b"])
    results = tool_results(model)
    seen_by_tools = "\n".join(results + [system_prompt(model)]).lower()
    for word in ("zebra", "pasta"):
        assert word not in seen_by_tools
    # Searching and counting by A's words found nothing in B's target. (The loop
    # adds a "(N tool calls left)" note after each result.)
    assert all(r.startswith("No reviews match.") for r in results[:4])
    # The phrase reached the model only inside B's own question.
    assert all(
        isinstance(m, HumanMessage) for m in messages_containing(model, LEAK_PHRASE)
    )

    # Nothing of A's comes back in the response, and the evidence is B's reviews.
    for text in (LEAK_PHRASE, "zebra", "pasta"):
        assert text.lower() not in response.text.lower()
    assert {e["id"] for e in body["evidence"]} == review_ids(conn, data["target_b"])


def test_user_a_gets_own_reviews_but_never_user_b_phrase(
    api, use_model, data, conn, make_token
):
    question = f"What do reviewers say about the {B_PHRASE} brunch?"
    model = leak_attempt(B_PHRASE, "mango", "waffle")
    use_model(model)

    response = ask(api, make_token, data["user_a"], data["target_a"], question)

    assert response.status_code == 200
    body = response.json()
    assert body["context"]["target_id"] == str(data["target_a"])

    # A's own reviews do reach the model, so the test above is not passing only
    # because nothing is ever sent.
    assert sent_texts(model) == stored_texts(conn, data["target_a"])
    assert any(LEAK_PHRASE in r for r in tool_results(model))

    # B's phrase and its words never reach A's context or A's response.
    seen_by_tools = "\n".join(tool_results(model) + [system_prompt(model)]).lower()
    for word in ("mango", "waffle"):
        assert word not in seen_by_tools
    assert all(
        isinstance(m, HumanMessage) for m in messages_containing(model, B_PHRASE)
    )
    for text in (B_PHRASE, "mango", "waffle"):
        assert text.lower() not in response.text.lower()
    assert {e["id"] for e in body["evidence"]} == review_ids(conn, data["target_a"])


# --- Interleaved requests ---------------------------------------------------------------


class ModelsInOrder:
    """A model factory that hands out the next scripted model on each call."""

    def __init__(self, *models):
        self.models, self.made = list(models), 0

    def __call__(self):
        self.made += 1
        return self.models.pop(0)


def assert_only_own_context(conn, model, response, own_target, other_words):
    """The model saw exactly `own_target`'s reviews, nothing with `other_words`
    reached it or the response, and the answer is about `own_target` only."""
    assert response.status_code == 200
    body = response.json()
    assert body["context"]["target_id"] == str(own_target)
    assert sent_texts(model) == stored_texts(conn, own_target)
    seen_by_tools = "\n".join(tool_results(model) + [system_prompt(model)]).lower()
    for word in other_words:
        assert word.lower() not in seen_by_tools
        assert word.lower() not in response.text.lower()
    assert {e["id"] for e in body["evidence"]} == review_ids(conn, own_target)


def test_interleaved_questions_from_two_users_stay_separate(
    api, data, conn, make_token
):
    """A asks, then B, then A again, each with a new recording model.

    INTERLEAVED, NOT CONCURRENT: the three requests run one after another in one
    process. This shows nothing carries over from one request to the next (the
    model, its messages, the R1/R2 labels, the tool set, the context). It does not
    test requests running at the same time."""
    model_a1 = FakeChatModel(
        call("search_reviews", **READ_ALL),
        submit("ANSWERED", "Here is what these reviews say.", cited=["R1", "R2"]),
    )
    model_b = leak_attempt(LEAK_PHRASE, "zebra", "pasta")
    model_a2 = leak_attempt(B_PHRASE, "mango", "waffle")
    factory = ModelsInOrder(model_a1, model_b, model_a2)
    app.dependency_overrides[get_model_factory] = lambda: factory

    first_a = ask(
        api, make_token, data["user_a"], data["target_a"], "What do people say?"
    )
    then_b = ask(
        api,
        make_token,
        data["user_b"],
        data["target_b"],
        f"What do reviewers say about the {LEAK_PHRASE} special?",
    )
    again_a = ask(
        api,
        make_token,
        data["user_a"],
        data["target_a"],
        f"What do reviewers say about the {B_PHRASE} brunch?",
    )

    # One new model per request, each used, none left over.
    assert factory.made == 3 and factory.models == []
    assert all(m.calls for m in (model_a1, model_b, model_a2))

    b_words = (B_PHRASE, "mango", "waffle")
    a_words = (LEAK_PHRASE, "zebra", "pasta")
    assert_only_own_context(conn, model_a1, first_a, data["target_a"], b_words)
    assert_only_own_context(conn, model_b, then_b, data["target_b"], a_words)
    # Right after B's request, A's R1 and R2 are A's reviews again: labels start
    # fresh for each question.
    assert_only_own_context(conn, model_a2, again_a, data["target_a"], b_words)
