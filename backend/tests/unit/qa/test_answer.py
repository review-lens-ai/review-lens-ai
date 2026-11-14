"""S2-010: the answer built from the model's submit_answer.

Each test gives build_answer one SubmitAnswer (or none) and checks the
AnswerResponse: evidence read back from the reviews the labels point to, no
ungrounded answers, and refusals without evidence.
"""

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.qa import AnswerStatus, RefusalReason, ToolStep
from app.services.qa.answer import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    MAX_CITATIONS,
    MAX_SUGGESTIONS,
    OUT_OF_SCOPE_MESSAGE,
    SubmitAnswer,
    build_answer,
    submit_answer_spec,
)
from app.services.qa.tools.guard import ToolScopeError
from app.services.qa.tools.labels import LabelBook
from tests.unit.qa.conftest import (
    OWNER_A,
    TARGET_A,
    TARGET_B,
    FakeSearch,
    FakeTargets,
    rows_of,
)

TARGET = {"id": TARGET_A, "name": "Luigi's Trattoria", "platform": "google_maps"}
A_ROWS = rows_of(TARGET_A)  # newest first: LEAK special, slow service, great food


def answer(submitted, *, labelled=A_ROWS, steps=(), total=3):
    """build_answer after a question whose tools showed `labelled` (R1, R2, ...)."""
    labels = LabelBook()
    for row in labelled:
        labels.source_for(row["id"], "detail")
    return build_answer(
        submitted,
        target=TARGET,
        owner_id=OWNER_A,
        reviews_total=total,
        labels=labels,
        search=FakeSearch(FakeTargets()),
        shown_review_ids={row["id"] for row in labelled},
        steps=list(steps),
    )


def reply(status, text="Mixed views.", cited=(), **extra):
    return SubmitAnswer(status=status, answer=text, cited=list(cited), **extra)


def test_answered_returns_the_cited_reviews_in_citation_order():
    result = answer(reply("ANSWERED", cited=["R3", "R1"]))

    assert result.status is AnswerStatus.ANSWERED
    assert result.answer == "Mixed views."
    assert [e.id for e in result.evidence] == [A_ROWS[2]["id"], A_ROWS[0]["id"]]
    # The text is the stored review, not anything the model wrote.
    assert result.evidence[0].review_text == "Great food, a bit loud on weekends."
    assert result.evidence[0].rating == 4
    assert result.refusal_reason is None
    assert result.suggestions == []


def test_a_label_no_tool_returned_is_dropped():
    result = answer(reply("ANSWERED", cited=["R9", "R2"]))

    assert [e.id for e in result.evidence] == [A_ROWS[1]["id"]]


def test_a_label_cited_twice_appears_once():
    result = answer(reply("ANSWERED", cited=["R1", " R1 "]))

    assert len(result.evidence) == 1


def test_evidence_is_capped():
    many = [dict(A_ROWS[0], id=row_id) for row_id in _ids(MAX_CITATIONS + 3)]
    labels = [f"R{n}" for n in range(1, len(many) + 1)]
    search = FakeSearch(FakeTargets())
    search.get_many = lambda target_id, owner_id, ids: [
        r for r in many if r["id"] in set(ids)
    ]
    book = LabelBook()
    for row in many:
        book.source_for(row["id"], "detail")

    result = build_answer(
        reply("ANSWERED", cited=labels),
        target=TARGET,
        owner_id=OWNER_A,
        reviews_total=len(many),
        labels=book,
        search=search,
        shown_review_ids={r["id"] for r in many},
        steps=[],
    )

    assert len(result.evidence) == MAX_CITATIONS


def test_a_cited_review_of_another_target_stops_the_answer():
    search = FakeSearch(FakeTargets())
    search.get_many = lambda target_id, owner_id, ids: rows_of(TARGET_B)
    book = LabelBook()
    book.source_for(A_ROWS[0]["id"], "detail")

    with pytest.raises(ToolScopeError):
        build_answer(
            reply("ANSWERED", cited=["R1"]),
            target=TARGET,
            owner_id=OWNER_A,
            reviews_total=3,
            labels=book,
            search=search,
            shown_review_ids=set(),
            steps=[],
        )


@pytest.mark.parametrize(
    ("text", "cited"),
    [
        ("Customers love it.", []),  # no citation
        ("Customers love it.", ["R9"]),  # only a label no tool returned
        ("   ", ["R1"]),  # no text
    ],
    ids=["no-citation", "only-unknown-labels", "blank-answer"],
)
def test_an_ungrounded_answer_becomes_insufficient_evidence(text, cited):
    result = answer(reply("ANSWERED", text, cited))

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.evidence == []
    assert result.refusal_reason is RefusalReason.NO_EVIDENCE


def test_no_reply_from_the_loop_is_insufficient_evidence():
    result = answer(None)

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE


def test_a_refusal_keeps_its_reason_and_explanation_and_never_has_evidence():
    result = answer(
        reply(
            "OUT_OF_SCOPE",
            "  I only have Luigi's Google Maps reviews.  ",
            cited=["R1"],
            refusal_reason="OTHER_PLATFORM",
        )
    )

    assert result.status is AnswerStatus.OUT_OF_SCOPE
    assert result.answer == "I only have Luigi's Google Maps reviews."
    assert result.evidence == []
    assert result.refusal_reason is RefusalReason.OTHER_PLATFORM


def test_insufficient_evidence_without_a_reason_is_no_evidence():
    result = answer(reply("INSUFFICIENT_EVIDENCE", "Parking isn't mentioned."))

    assert result.refusal_reason is RefusalReason.NO_EVIDENCE


@pytest.mark.parametrize(
    ("status", "fallback"),
    [
        ("INSUFFICIENT_EVIDENCE", INSUFFICIENT_EVIDENCE_MESSAGE),
        ("OUT_OF_SCOPE", OUT_OF_SCOPE_MESSAGE),
    ],
)
def test_a_refusal_without_text_gets_a_fixed_message(status, fallback):
    assert answer(reply(status, "")).answer == fallback


def test_suggestions_are_trimmed_and_capped_on_refusals_only():
    suggestions = ["  What about the fries? ", "", "Wait times?", "Staff?", "Prices?"]
    refusal = answer(reply("OUT_OF_SCOPE", "No.", suggestions=suggestions))
    answered = answer(reply("ANSWERED", cited=["R1"], suggestions=suggestions))

    assert refusal.suggestions == ["What about the fries?", "Wait times?", "Staff?"]
    assert len(refusal.suggestions) == MAX_SUGGESTIONS
    assert answered.suggestions == []


def test_context_counts_come_from_the_question_not_the_reply():
    result = answer(
        reply("ANSWERED", "There are 999 reviews.", ["R1"]),
        labelled=A_ROWS[:1],
        total=40,
    )

    assert result.context.target_id == TARGET_A
    assert result.context.target_name == "Luigi's Trattoria"
    assert result.context.reviews_considered == 1
    assert result.context.reviews_total == 40
    assert result.context.is_subset is True


def test_the_tool_steps_are_returned_for_the_ui():
    steps = [ToolStep(tool="count_reviews", summary="1 of 3 reviews")]
    result = answer(reply("ANSWERED", cited=["R1"]), steps=steps)

    assert result.steps == steps


def test_submit_answer_spec_is_named_and_lists_its_fields():
    spec = submit_answer_spec()

    assert spec["name"] == "submit_answer"
    assert {"status", "answer", "cited", "refusal_reason", "suggestions"} <= set(
        spec["parameters"]["properties"]
    )


@pytest.mark.parametrize(
    "args",
    [
        {"status": "MAYBE", "answer": "Hmm."},
        {
            "status": "NO_REVIEWS",
            "answer": "None.",
        },  # the app decides this, not the model
        {"status": "ANSWERED", "answer": "Ok.", "extra": 1},
        {"answer": "No status."},
    ],
    ids=[
        "unknown-status",
        "no-reviews-is-not-a-model-status",
        "extra-field",
        "missing-status",
    ],
)
def test_submit_answer_rejects_what_the_model_may_not_send(args):
    with pytest.raises(ValidationError):
        SubmitAnswer.model_validate(args)


def _ids(n):
    return [UUID(int=i + 1) for i in range(n)]
