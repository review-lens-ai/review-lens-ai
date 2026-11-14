"""S2-010: what answer_question sends to the model, and when it sends nothing.

The real orchestrator, loop, prompt and answer builder run on in-memory data; only
the model is the recording FakeChatModel. These cover the S2-003 checks at the
orchestration level (right instructions, right reviews, no wrong-target or
cross-user reviews, correct handling of each reply) and the stops before the model.
"""

import logging
from types import SimpleNamespace

import pytest
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.errors import NotFoundError
from app.llm import ModelUnavailableError
from app.schemas.qa import AnswerStatus
from app.services.qa import (
    InvalidQuestionError,
    NoReviewsError,
    QAConfig,
    QAContextError,
    QAUnavailableError,
    answer_question,
)
from tests.unit.qa.conftest import (
    LEAK_PHRASE,
    OWNER_A,
    OWNER_B,
    TARGET_A,
    TARGET_A2,
    TARGET_B,
    TARGET_EMPTY,
    FakeChatModel,
    call,
    fake_toolset,
    rows_of,
    submit,
)


class CountingFactory:
    """make_model that counts how often the model was made."""

    def __init__(self, model):
        self.model, self.made = model, 0

    def __call__(self):
        self.made += 1
        return self.model


# --- What the model is sent -------------------------------------------------------


def test_the_model_gets_the_rules_and_briefing_then_the_question(ask):
    model = FakeChatModel(submit("OUT_OF_SCOPE", "No."))
    ask(model, question="  What do people say about the food?  ")

    system, human = model.calls[0]["messages"]
    assert isinstance(system, SystemMessage)
    assert '"Luigi\'s Trattoria"' in system.content
    assert "Saved reviews: 3." in system.content
    assert human == HumanMessage("What do people say about the food?")


def test_the_model_is_offered_the_tool_set_and_submit_answer(ask):
    model = FakeChatModel(submit("OUT_OF_SCOPE", "No."))
    ask(model)

    assert model.last["tools"] == ["find_reviews", "count_reviews", "submit_answer"]


def test_review_text_reaches_the_model_only_through_tool_results(ask):
    model = FakeChatModel(call("find_reviews"), submit("ANSWERED", cited=["R1"]))
    ask(model)

    system = model.calls[0]["messages"][0].content
    assert all(row["review_text"] not in system for row in rows_of(TARGET_A))
    tool_result = model.last["messages"][-1].content
    assert all(row["review_text"] in tool_result for row in rows_of(TARGET_A))


def test_a_grounded_answer_returns_the_cited_stored_reviews(ask):
    model = FakeChatModel(
        call("find_reviews", words="slow"),
        submit("ANSWERED", "Service is slow.", cited=["R1"]),
    )
    result = ask(model)

    assert result.status is AnswerStatus.ANSWERED
    assert [e.review_text for e in result.evidence] == [
        "Service was slow and the waiter forgot our drinks."
    ]
    assert [s.tool for s in result.steps] == ["find_reviews"]
    assert result.context.target_id == TARGET_A
    assert result.context.reviews_total == 3


# --- Wrong-target and cross-user reviews (S2-BR-022 to S2-BR-024) ------------------


def test_switching_target_sends_only_the_new_targets_reviews(ask):
    first = FakeChatModel(call("find_reviews"), submit("ANSWERED", cited=["R1"]))
    second = FakeChatModel(call("find_reviews"), submit("ANSWERED", cited=["R1"]))
    ask(first, target_id=TARGET_A)
    ask(second, target_id=TARGET_A2)

    sent = second.sent_text()
    assert all(row["review_text"] in sent for row in rows_of(TARGET_A2))
    assert LEAK_PHRASE not in sent
    assert "Luigi's Trattoria" not in sent


def test_user_a_phrase_never_reaches_user_b(ask):
    model = FakeChatModel(
        call("find_reviews", words=LEAK_PHRASE),
        call("find_reviews"),
        submit("INSUFFICIENT_EVIDENCE", "B's reviews don't mention it."),
    )
    ask(
        model,
        owner_id=OWNER_B,
        target_id=TARGET_B,
        question="What do reviewers say about the special?",
    )

    assert LEAK_PHRASE not in model.sent_text()
    assert "No reviews match." in model.sent_text()


def test_user_b_asking_about_user_a_target_is_not_found_and_no_model_is_made(repos):
    model = FakeChatModel()
    factory = CountingFactory(model)
    targets, reviews, search = repos
    with pytest.raises(NotFoundError):
        answer_question(
            owner_id=OWNER_B,
            target_id=TARGET_A,
            question="What is good here?",
            targets=targets,
            reviews=reviews,
            search=search,
            make_model=factory,
            make_toolset=fake_toolset,
        )
    assert factory.made == 0
    assert model.calls == []


def test_a_review_of_another_target_stops_the_question_before_more_is_sent(ask, caplog):
    def leaky(search, owner_id, target_id):
        return fake_toolset(search, owner_id, target_id, leak_from=TARGET_B)

    model = FakeChatModel(call("find_reviews"), submit("ANSWERED", cited=["R1"]))
    with caplog.at_level(logging.ERROR), pytest.raises(QAContextError):
        ask(model, make_toolset=leaky)

    assert len(model.calls) == 1
    assert "nothing more was sent to the model" in caplog.text


# --- Stops before the model ---------------------------------------------------------


def test_a_target_without_reviews_is_409_and_no_model_is_made(repos):
    targets, reviews, search = repos
    factory = CountingFactory(FakeChatModel())
    with pytest.raises(NoReviewsError) as exc:
        answer_question(
            owner_id=OWNER_A,
            target_id=TARGET_EMPTY,
            question="What is good here?",
            targets=targets,
            reviews=reviews,
            search=search,
            make_model=factory,
        )

    assert exc.value.status_code == 409
    assert exc.value.code == "QA_NO_REVIEWS"
    assert factory.made == 0


@pytest.mark.parametrize("question", ["", "   "])
def test_a_blank_question_is_rejected_before_the_model(ask, question):
    model = FakeChatModel()
    with pytest.raises(InvalidQuestionError):
        ask(model, question=question)

    assert model.calls == []


# --- Model failures -------------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ModelUnavailableError("upstream 529: overloaded, key sk-secret-123"),
        ModelUnavailableError("read timeout after 30s, key sk-secret-123"),
    ],
    ids=["unavailable", "timeout"],
)
def test_a_model_failure_is_503_without_provider_text(ask, error, caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(QAUnavailableError) as exc:
        ask(FakeChatModel(error))

    assert exc.value.status_code == 503
    assert exc.value.code == "QA_UNAVAILABLE"
    assert "sk-secret-123" not in exc.value.message
    assert "ModelUnavailableError" in caplog.text
    assert "sk-secret-123" not in caplog.text


def test_a_model_that_is_not_configured_is_503(repos):
    targets, reviews, search = repos

    def not_configured():
        raise ModelUnavailableError("OPENROUTER_API_KEY and QA_MODEL must be set")

    with pytest.raises(QAUnavailableError):
        answer_question(
            owner_id=OWNER_A,
            target_id=TARGET_A,
            question="What is good here?",
            targets=targets,
            reviews=reviews,
            search=search,
            make_model=not_configured,
            make_toolset=fake_toolset,
        )


def test_an_unexpected_error_is_not_hidden(ask):
    # Only ModelError means "the provider failed"; a bug is raised as itself.
    with pytest.raises(RuntimeError):
        ask(FakeChatModel(RuntimeError("bug in adapter")))


# --- Full mode (the fallback) ---------------------------------------------------------


def test_full_mode_sends_every_review_and_only_submit_answer(ask):
    model = FakeChatModel(submit("ANSWERED", "Slow service.", cited=["R2"]))
    result = ask(model, config=QAConfig(mode="full"))

    system, human = model.calls[0]["messages"]
    assert "listed in the user message" in system.content
    assert human.content.startswith("All 3 reviews:")
    assert human.content.endswith("Question: What do people say about the food?")
    assert model.last["tools"] == ["submit_answer"]
    assert model.last["tool_choice"] == "submit_answer"
    # R2 is the second-newest review; the label maps back to the stored text.
    assert result.evidence[0].review_text.startswith("Service was slow")
    assert result.context.reviews_considered == 3
    assert result.context.is_subset is False


def test_full_mode_uses_tools_for_a_target_too_big_for_one_request(ask):
    model = FakeChatModel(submit("OUT_OF_SCOPE", "No."))
    ask(model, config=QAConfig(mode="full", full_mode_max_characters=200))

    system, human = model.calls[0]["messages"]
    assert "through the tools" in system.content
    assert human.content == "What do people say about the food?"
    assert model.last["tools"] == ["find_reviews", "count_reviews", "submit_answer"]


def test_config_is_read_from_settings():
    settings = SimpleNamespace(
        qa_max_tool_calls=4,
        qa_timeout_seconds=12.5,
        qa_mode="full",
        qa_full_mode_max_characters=5000,
    )

    assert QAConfig.from_settings(settings) == QAConfig(
        max_tool_calls=4,
        timeout_seconds=12.5,
        mode="full",
        full_mode_max_characters=5000,
    )
