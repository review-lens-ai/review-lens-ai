"""S2-003 check 5: application behavior for controlled model responses.

Each test scripts one ModelReply and checks the AnswerResponse ReviewLens
returns: evidence only from the reviews that were sent, no ungrounded answers,
and refusals without evidence.
"""

from datetime import UTC, datetime

import pytest

from app.llm import ModelReply, ModelReplyError, ReplyStatus
from app.schemas.qa import AnswerStatus
from app.services.qa.answer import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    MAX_CITATIONS,
    OUT_OF_SCOPE_MESSAGE,
    build_answer,
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
