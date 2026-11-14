"""S2-003 check 5: application behavior for controlled model responses.

Each test scripts one ModelReply and checks the AnswerResponse ReviewLens
returns: evidence only from the reviews that were sent, no ungrounded answers,
and refusals without evidence.
"""

from datetime import UTC, datetime

import pytest

from app.llm import ModelReply, ModelReplyError, ReplyStatus
from app.schemas.qa import AnswerStatus, RefusalReason
from app.services.qa.answer import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    MAX_CITATIONS,
    MAX_REFUSAL_CHARACTERS,
    MAX_SUGGESTIONS,
    OUT_OF_SCOPE_MESSAGE,
    build_answer,
    no_reviews_answer,
)
from app.services.qa.context import build_bundle

TARGET = {"id": "10000000-0000-0000-0000-000000000001", "name": "Luigi's",
          "platform": "google_maps"}  # fmt: skip


def review_id(n: int) -> str:
    return f"a0000000-0000-0000-0000-{n:012d}"


def bundle(count=3, total=None):
    rows = [
        {
            "id": review_id(n),
            "target_id": TARGET["id"],
            "rating": 1 + n % 5,
            "review_text": f"Review number {n}.",
            "reviewed_at": datetime(2026, 10, 1, tzinfo=UTC),
        }
        for n in range(1, count + 1)
    ]
    return build_bundle(TARGET, rows, total if total is not None else count)


def reply(status, answer="Some text.", *ids):
    return ModelReply(status, answer, tuple(ids))


def test_answered_returns_cited_reviews_in_citation_order_from_the_context():
    result = build_answer(
        reply(ReplyStatus.ANSWERED, "Mixed views.", review_id(3), review_id(1)),
        bundle(),
    )

    assert result.status is AnswerStatus.ANSWERED
    assert result.answer == "Mixed views."
    assert [str(e.id) for e in result.evidence] == [review_id(3), review_id(1)]
    # Text comes from the stored review, not from the model.
    assert result.evidence[0].review_text == "Review number 3."
    assert result.evidence[0].rating == 4


def test_cited_id_not_in_the_context_is_dropped():
    made_up = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    result = build_answer(
        reply(ReplyStatus.ANSWERED, "Ok.", made_up, review_id(2)), bundle()
    )

    assert [str(e.id) for e in result.evidence] == [review_id(2)]


def test_duplicate_citations_appear_once():
    result = build_answer(
        reply(ReplyStatus.ANSWERED, "Ok.", review_id(1), review_id(1)), bundle()
    )

    assert len(result.evidence) == 1


def test_evidence_is_capped():
    ids = [review_id(n) for n in range(1, MAX_CITATIONS + 6)]
    result = build_answer(
        reply(ReplyStatus.ANSWERED, "Many.", *ids), bundle(count=MAX_CITATIONS + 5)
    )

    assert [str(e.id) for e in result.evidence] == ids[:MAX_CITATIONS]


@pytest.mark.parametrize(
    ("answer", "ids"),
    [
        ("Customers love it.", ()),  # no citation at all
        (
            "Customers love it.",
            ("ffffffff-ffff-ffff-ffff-ffffffffffff",),
        ),  # only made up
        ("   ", (review_id(1),)),  # no text
    ],
    ids=["no-citations", "only-unknown-citations", "blank-answer"],
)
def test_ungrounded_answer_becomes_insufficient_evidence(answer, ids):
    result = build_answer(reply(ReplyStatus.ANSWERED, answer, *ids), bundle())

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.evidence == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ReplyStatus.INSUFFICIENT_EVIDENCE, AnswerStatus.INSUFFICIENT_EVIDENCE),
        (ReplyStatus.OUT_OF_SCOPE, AnswerStatus.OUT_OF_SCOPE),
    ],
)
def test_refusal_keeps_the_explanation_and_never_carries_evidence(status, expected):
    result = build_answer(
        reply(status, "  I can only discuss these reviews.  ", review_id(1)), bundle()
    )

    assert result.status is expected
    assert result.answer == "I can only discuss these reviews."
    assert result.evidence == []


@pytest.mark.parametrize(
    ("status", "fallback"),
    [
        (ReplyStatus.INSUFFICIENT_EVIDENCE, INSUFFICIENT_EVIDENCE_MESSAGE),
        (ReplyStatus.OUT_OF_SCOPE, OUT_OF_SCOPE_MESSAGE),
    ],
)
def test_refusal_without_text_gets_a_fixed_message(status, fallback):
    result = build_answer(reply(status, ""), bundle())

    assert result.answer == fallback


def test_unknown_status_is_a_reply_error():
    with pytest.raises(ModelReplyError):
        build_answer(ModelReply("MAYBE", "Hmm."), bundle())  # type: ignore[arg-type]


def test_context_counts_come_from_the_bundle_not_the_reply():
    result = build_answer(
        reply(ReplyStatus.ANSWERED, "There are 999 reviews.", review_id(1)),
        bundle(count=3, total=40),
    )

    assert result.context.reviews_considered == 3
    assert result.context.reviews_total == 40
    assert result.context.is_subset is True


# --- Refusal reasons and suggestions (S2-012, S2-014, S2-015, S2-BR-021) ---


def refusal(status, reason=None, suggestions=()):
    return ModelReply(status, "I can't answer that.", (), reason, tuple(suggestions))


def test_an_answer_has_no_refusal_reason_or_suggestions():
    result = build_answer(
        ModelReply(
            ReplyStatus.ANSWERED,
            "People like the fries.",
            (review_id(1),),
            RefusalReason.OTHER_ENTITY,
            ("Ask about the fries?",),
        ),
        bundle(),
    )

    assert result.status is AnswerStatus.ANSWERED
    assert result.refusal_reason is None
    assert result.suggestions == []


@pytest.mark.parametrize(
    "reason",
    [
        RefusalReason.GENERAL_KNOWLEDGE,
        RefusalReason.OTHER_ENTITY,
        RefusalReason.OTHER_LOCATION,
        RefusalReason.OTHER_PLATFORM,
    ],
)
def test_out_of_scope_keeps_the_model_reason(reason):
    result = build_answer(refusal(ReplyStatus.OUT_OF_SCOPE, reason), bundle())

    assert result.status is AnswerStatus.OUT_OF_SCOPE
    assert result.refusal_reason is reason


@pytest.mark.parametrize(
    "reason",
    [None, RefusalReason.NO_EVIDENCE, "RUDE"],
    ids=["no-reason", "evidence-reason", "unknown-reason"],
)
def test_out_of_scope_without_a_matching_reason_still_refuses(reason):
    # The refusal stands; only the reason is left out (no crash, no answer).
    result = build_answer(refusal(ReplyStatus.OUT_OF_SCOPE, reason), bundle())

    assert result.status is AnswerStatus.OUT_OF_SCOPE
    assert result.refusal_reason is None


@pytest.mark.parametrize("reason", [None, RefusalReason.OTHER_ENTITY])
def test_insufficient_evidence_always_has_reason_no_evidence(reason):
    result = build_answer(refusal(ReplyStatus.INSUFFICIENT_EVIDENCE, reason), bundle())

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.refusal_reason is RefusalReason.NO_EVIDENCE


def test_an_ungrounded_answer_is_reported_as_no_evidence():
    result = build_answer(reply(ReplyStatus.ANSWERED, "Customers love it."), bundle())

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.refusal_reason is RefusalReason.NO_EVIDENCE


def test_suggestions_are_trimmed_deduplicated_and_capped():
    result = build_answer(
        refusal(
            ReplyStatus.OUT_OF_SCOPE,
            RefusalReason.OTHER_PLATFORM,
            ["  What's the rating?  ", "", "What's the rating?", "Fries?", "Staff?"],
        ),
        bundle(),
    )

    assert result.suggestions == ["What's the rating?", "Fries?"]
    assert len(result.suggestions) == MAX_SUGGESTIONS


def test_a_target_without_reviews_has_no_refusal_reason():
    result = no_reviews_answer(bundle(count=0))

    assert result.status is AnswerStatus.NO_REVIEWS
    assert result.refusal_reason is None
    assert result.suggestions == []


# --- Refusal templates and the refusal check (S2-013, S2-BR-021) ---

ADDRESS = "12 Mulberry St, New York, NY 10013"


def placed_bundle(address=ADDRESS):
    rows = [
        {
            "id": review_id(1),
            "target_id": TARGET["id"],
            "rating": 4,
            "review_text": "Good.",
            "reviewed_at": None,
        }
    ]
    return build_bundle(TARGET, rows, 1, address=address)


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (
            ReplyStatus.OUT_OF_SCOPE,
            RefusalReason.GENERAL_KNOWLEDGE,
            "I can only answer questions about the Google Maps reviews for "
            "Luigi's, so I can't help with that.",
        ),
        (
            ReplyStatus.OUT_OF_SCOPE,
            RefusalReason.OTHER_ENTITY,
            "I only have reviews for Luigi's, so I can't speak to other "
            "businesses, products or people.",
        ),
        (
            ReplyStatus.OUT_OF_SCOPE,
            RefusalReason.OTHER_LOCATION,
            f"These reviews are for Luigi's at {ADDRESS} only, not other locations.",
        ),
        (
            ReplyStatus.OUT_OF_SCOPE,
            RefusalReason.OTHER_PLATFORM,
            "I only have Google Maps reviews for Luigi's, not reviews from other "
            "sites.",
        ),
        (
            ReplyStatus.INSUFFICIENT_EVIDENCE,
            RefusalReason.NO_EVIDENCE,
            INSUFFICIENT_EVIDENCE_MESSAGE,
        ),
    ],
)
def test_a_blank_refusal_gets_the_template_for_its_reason(status, reason, expected):
    result = build_answer(ModelReply(status, "", (), reason), placed_bundle())

    assert result.answer == expected
    assert result.refusal_reason is reason


def test_the_location_template_works_without_an_address():
    result = build_answer(
        ModelReply(ReplyStatus.OUT_OF_SCOPE, "", (), RefusalReason.OTHER_LOCATION),
        placed_bundle(address=None),
    )

    assert result.answer == "These reviews are for Luigi's only, not other locations."


def test_a_brief_refusal_from_the_model_is_kept():
    text = "I only have Google Maps reviews for Luigi's, not Yelp."
    result = build_answer(
        ModelReply(ReplyStatus.OUT_OF_SCOPE, text, (), RefusalReason.OTHER_PLATFORM),
        placed_bundle(),
    )

    assert result.answer == text


def test_a_refusal_that_runs_long_is_replaced_by_the_template():
    # A long refusal has most likely answered the question anyway.
    leaky = (
        "I can't say from the reviews, but generally " + "x" * MAX_REFUSAL_CHARACTERS
    )
    result = build_answer(
        ModelReply(
            ReplyStatus.OUT_OF_SCOPE, leaky, (), RefusalReason.GENERAL_KNOWLEDGE
        ),
        placed_bundle(),
    )

    assert result.answer.startswith("I can only answer questions about")
    assert "generally" not in result.answer


def test_a_refusal_at_the_length_limit_is_kept():
    text = "x" * MAX_REFUSAL_CHARACTERS
    result = build_answer(
        ModelReply(ReplyStatus.OUT_OF_SCOPE, text, (), RefusalReason.OTHER_ENTITY),
        placed_bundle(),
    )

    assert result.answer == text


@pytest.mark.parametrize(
    ("status", "reason", "suggestion"),
    [
        (ReplyStatus.OUT_OF_SCOPE, RefusalReason.GENERAL_KNOWLEDGE,
         "What do reviewers like most?"),
        (ReplyStatus.OUT_OF_SCOPE, RefusalReason.OTHER_ENTITY,
         "What do reviewers like most here?"),
        (ReplyStatus.OUT_OF_SCOPE, RefusalReason.OTHER_LOCATION,
         "What do reviewers say about the service here?"),
        (ReplyStatus.OUT_OF_SCOPE, RefusalReason.OTHER_PLATFORM,
         "What's the overall rating on Google Maps?"),
        (ReplyStatus.INSUFFICIENT_EVIDENCE, None,
         "What do reviewers mention most often?"),
    ],
)  # fmt: skip
def test_a_refusal_without_suggestions_gets_one_for_its_reason(
    status, reason, suggestion
):
    result = build_answer(ModelReply(status, "No.", (), reason), placed_bundle())

    assert result.suggestions == [suggestion]


def test_the_models_own_suggestions_win_over_the_fallback():
    result = build_answer(
        ModelReply(
            ReplyStatus.OUT_OF_SCOPE,
            "No.",
            (),
            RefusalReason.OTHER_PLATFORM,
            ("How is the pasta?",),
        ),
        placed_bundle(),
    )

    assert result.suggestions == ["How is the pasta?"]


def test_an_out_of_scope_refusal_without_a_reason_gets_no_fallback_suggestion():
    # Without a reason there is no safe suggestion to make; the general message stays.
    result = build_answer(ModelReply(ReplyStatus.OUT_OF_SCOPE, ""), placed_bundle())

    assert result.answer == OUT_OF_SCOPE_MESSAGE
    assert result.suggestions == []


def test_a_downgraded_answer_gets_the_no_evidence_suggestion():
    result = build_answer(reply(ReplyStatus.ANSWERED, "Customers love it."), bundle())

    assert result.suggestions == ["What do reviewers mention most often?"]
