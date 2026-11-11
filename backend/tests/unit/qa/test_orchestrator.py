"""S2-003: what answer_question sends to the model, and when it sends nothing.

Covers the issue's checks 2 to 5 at the orchestration level: the right reviews
are supplied, wrong-target and cross-user reviews are not, and every stop before
the model really leaves the model uncalled. The prompt itself is in
test_prompt.py and reply handling in test_answer.py.
"""

import logging
from uuid import UUID

import pytest

from app.core.errors import NotFoundError
from app.llm import ModelReplyError, ModelUnavailableError
from app.schemas.qa import AnswerStatus
from app.services.qa import InvalidQuestionError, QAUnavailableError
from app.services.qa.context import ContextIsolationError
from app.services.qa.prompt import MAX_QUESTION_LENGTH
from tests.unit.qa.conftest import (
    LEAK_PHRASE,
    OWNER_A,
    OWNER_B,
    TARGET_A,
    TARGET_A2,
    TARGET_B,
    TARGET_EMPTY,
    FakeModel,
    answered,
    review_ids,
)


def sent_ids(request) -> list[str]:
    """Review ids in the order they appear in the request's review block."""
    return [
        line.split('id="')[1].split('"')[0]
        for line in request.reviews_block.splitlines()
        if line.startswith("<review id=")
    ]


# --- Correct context ---------------------------------------------------------


def test_model_receives_every_review_of_the_active_target(ask):
    model = FakeModel()
    ask(model)

    assert len(model.requests) == 1
    assert sent_ids(model.last) == review_ids(TARGET_A)


def test_question_is_sent_trimmed(ask):
    model = FakeModel()
    ask(model, question="   What do people say about the service?  ")

    assert model.last.question == "What do people say about the service?"


def test_history_is_empty_until_conversations_are_stored(ask):
    model = FakeModel()
    ask(model)

    assert model.last.history == ()


def test_answer_context_reports_the_active_target_and_counts(ask):
    result = ask(FakeModel(answered(review_ids(TARGET_A)[0])))

    assert str(result.context.target_id) == TARGET_A
    assert result.context.target_name == "Luigi's Trattoria"
    assert result.context.reviews_considered == 3
    assert result.context.reviews_total == 3
    assert result.context.is_subset is False


# --- Wrong-target reviews (S2-BR-023, S2-BR-024) ------------------------------


def test_switching_target_sends_only_the_new_targets_reviews(ask):
    model = FakeModel()
    ask(model, target_id=TARGET_A)
    ask(model, target_id=TARGET_A2)

    first, second = model.requests
    assert sent_ids(first) == review_ids(TARGET_A)
    assert sent_ids(second) == review_ids(TARGET_A2)
    assert LEAK_PHRASE not in second.reviews_block
    assert "Luigi's Trattoria" not in second.system_prompt


def test_selector_that_returns_another_targets_review_stops_before_the_model(
    ask, store, caplog
):
    def leaky_selector(reviews, target, owner_id, question):
        # A future selector that forgot its target filter.
        return reviews.list_for_target(target["id"], owner_id) + [
            {**store.reviews.rows[3], "target_id": TARGET_A2}
        ]

    model = FakeModel()
    with caplog.at_level(logging.ERROR), pytest.raises(ContextIsolationError):
        ask(model, select_reviews=leaky_selector)

    assert model.requests == []
    assert "nothing was sent to the model" in caplog.text


def test_selector_that_finds_nothing_answers_insufficient_without_the_model(ask):
    model = FakeModel()
    result = ask(model, select_reviews=lambda *args: [])

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.context.is_subset is True
    assert model.requests == []


# --- Cross-user reviews (S2-BR-022, S2-BR-026) --------------------------------


def test_user_b_asking_about_user_a_target_is_not_found_and_model_not_called(ask):
    model = FakeModel()
    with pytest.raises(NotFoundError):
        ask(model, owner_id=OWNER_B, target_id=TARGET_A)

    assert model.requests == []


def test_user_a_phrase_never_reaches_user_b_request(ask):
    model = FakeModel()
    ask(
        model,
        owner_id=OWNER_B,
        target_id=TARGET_B,
        question=f"What do reviewers say about the {LEAK_PHRASE} special?",
    )

    request = model.last
    assert sent_ids(request) == review_ids(TARGET_B)
    assert LEAK_PHRASE not in request.reviews_block
    assert LEAK_PHRASE not in request.system_prompt
    # The phrase reaches the model only inside B's own question, which B typed.


def test_user_b_cannot_get_user_a_review_back_through_a_citation(ask):
    model = FakeModel(answered(review_ids(TARGET_A)[0]))
    result = ask(model, owner_id=OWNER_B, target_id=TARGET_B)

    # The cited id is not in B's context, so nothing of A's is returned.
    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.evidence == []


# --- Stops before the model ----------------------------------------------------


def test_target_without_reviews_returns_no_reviews_without_calling_model(ask):
    model = FakeModel()
    result = ask(model, target_id=TARGET_EMPTY)

    assert result.status is AnswerStatus.NO_REVIEWS
    assert result.evidence == []
    assert result.context.reviews_total == 0
    assert model.requests == []


@pytest.mark.parametrize(
    "question", ["", "   ", "\n\t", "x" * (MAX_QUESTION_LENGTH + 1)]
)
def test_blank_or_too_long_question_is_rejected_without_calling_model(ask, question):
    model = FakeModel()
    with pytest.raises(InvalidQuestionError):
        ask(model, question=question)

    assert model.requests == []


# --- Provider failure ------------------------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        ModelUnavailableError("upstream 529: overloaded, key sk-secret-123"),
        # Adapters turn provider timeouts into ModelUnavailableError.
        ModelUnavailableError("read timeout after 30s, key sk-secret-123"),
        ModelReplyError("could not parse: {'answer': sk-secret-123"),
    ],
    ids=["unavailable", "timeout", "bad-reply"],
)
def test_model_failure_is_qa_unavailable_without_provider_text(ask, error, caplog):
    with caplog.at_level(logging.WARNING), pytest.raises(QAUnavailableError) as exc:
        ask(FakeModel(error=error))

    assert exc.value.code == "QA_UNAVAILABLE"
    assert str(error) not in exc.value.message
    # Server log names the error type only, never the provider's text.
    assert type(error).__name__ in caplog.text
    assert "sk-secret-123" not in caplog.text
    assert str(error) not in caplog.text


def test_unknown_reply_status_is_qa_unavailable(ask):
    model = FakeModel()
    model.reply = type(model.reply)("MAYBE", "Hmm.", ())  # status outside ReplyStatus

    with pytest.raises(QAUnavailableError):
        ask(model)


def test_unexpected_exception_from_model_is_not_hidden(ask):
    # Only ModelError means "the provider failed". A bug in an adapter is raised
    # as itself, so it reaches the generic 500 handler and the logs.
    with pytest.raises(RuntimeError):
        ask(FakeModel(error=RuntimeError("bug in adapter")))


def test_owner_and_target_ids_may_be_given_as_uuids(ask):
    model = FakeModel()
    ask(model, owner_id=UUID(OWNER_A), target_id=UUID(TARGET_A))

    assert sent_ids(model.last) == review_ids(TARGET_A)
