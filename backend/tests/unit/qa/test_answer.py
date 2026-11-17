"""S2-010, S2-013: the answer built from the model's submit_answer.

Each test gives build_answer one SubmitAnswer (or none) and checks the
AnswerResponse: evidence read back from the reviews the labels point to, no
ungrounded answers, refusals without evidence, and each refusal's reason, message
and suggestions.
"""

from uuid import UUID

import pytest
from pydantic import ValidationError

from app.schemas.qa import AnswerStatus, RefusalReason, ToolStep
from app.services.qa.answer import (
    INSUFFICIENT_EVIDENCE_MESSAGE,
    MAX_CITATIONS,
    MAX_REFUSAL_CHARACTERS,
    MAX_SUGGESTIONS,
    OUT_OF_SCOPE_MESSAGE,
    LoopRecord,
    SubmitAnswer,
    build_answer,
    no_reviews_answer,
    submit_answer_spec,
    tidy_answer,
)
from app.services.qa.context import build_bundle
from app.services.qa.tools.guard import ToolScopeError
from app.services.qa.tools.labels import LabelBook
from tests.unit.qa.conftest import TARGET_A, TARGET_B, FakeSearch, rows_of

TARGET = {"id": TARGET_A, "name": "Luigi's", "platform": "google_maps"}
A_ROWS = rows_of(TARGET_A)  # newest first: LEAK special, slow service, great food
ADDRESS = "12 Mulberry St, New York, NY 10013"


def answer(submitted, *, labelled=A_ROWS, steps=(), total=3, address=None, search=None):
    """build_answer after a question whose tools showed `labelled` (R1, R2, ...)."""
    labels = LabelBook()
    for row in labelled:
        labels.source_for(row["id"], "detail")
    return build_answer(
        submitted,
        bundle=build_bundle(TARGET, {"review_count": total}, address=address),
        labels=labels,
        search=search or FakeSearch(TARGET_A),
        record=LoopRecord(
            steps=tuple(steps),
            shown_review_ids=frozenset(row["id"] for row in labelled),
        ),
    )


def reply(status, text="Mixed views.", cited=(), **extra):
    return SubmitAnswer(status=status, answer=text, cited=list(cited), **extra)


def refusal(status, reason=None, suggestions=()):
    return reply(
        status,
        "I can't answer that.",
        refusal_reason=reason,
        suggestions=list(suggestions),
    )


class _Search:
    """A search whose get_many returns the given rows, whatever was asked."""

    target_id = TARGET_A

    def __init__(self, rows):
        self.rows = rows

    def get_many(self, review_ids):
        wanted = set(review_ids)
        return [row for row in self.rows if row["id"] in wanted] or self.rows


# --- Evidence: cited labels -> stored reviews (S2-BR-010) ---


def test_answered_returns_the_cited_reviews_in_citation_order():
    result = answer(reply("ANSWERED", cited=["R3", "R1"]))

    assert result.status is AnswerStatus.ANSWERED
    assert result.answer == "Mixed views."
    assert [e.id for e in result.evidence] == [A_ROWS[2]["id"], A_ROWS[0]["id"]]
    # The text is the stored review, not anything the model wrote.
    assert result.evidence[0].review_text == "Great food, a bit loud on weekends."
    assert result.evidence[0].rating == 4


def test_a_label_no_tool_returned_is_dropped():
    result = answer(reply("ANSWERED", cited=["R9", "R2"]))

    assert [e.id for e in result.evidence] == [A_ROWS[1]["id"]]


def test_a_label_cited_twice_appears_once():
    result = answer(reply("ANSWERED", cited=["R1", " R1 "]))

    assert len(result.evidence) == 1


def test_evidence_is_capped():
    many = [dict(A_ROWS[0], id=UUID(int=n)) for n in range(1, MAX_CITATIONS + 4)]
    labels = [f"R{n}" for n in range(1, len(many) + 1)]

    result = answer(
        reply("ANSWERED", cited=labels),
        labelled=many,
        total=len(many),
        search=_Search(many),
    )

    assert [e.id for e in result.evidence] == [r["id"] for r in many[:MAX_CITATIONS]]


def test_a_cited_review_of_another_target_stops_the_answer():
    with pytest.raises(ToolScopeError):
        answer(reply("ANSWERED", cited=["R1"]), search=_Search(rows_of(TARGET_B)))


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


def test_a_downgraded_answer_gets_the_no_evidence_suggestion():
    result = answer(reply("ANSWERED", "Customers love it."))

    assert result.suggestions == ["What do reviewers mention most often?"]


def test_no_reply_from_the_loop_is_insufficient_evidence():
    result = answer(None)

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.answer == INSUFFICIENT_EVIDENCE_MESSAGE
    assert result.refusal_reason is RefusalReason.NO_EVIDENCE


# --- Context and steps ---


def test_context_counts_come_from_the_question_not_the_reply():
    result = answer(
        reply("ANSWERED", "There are 999 reviews.", ["R1"]),
        labelled=A_ROWS[:1],
        total=40,
    )

    assert result.context.target_id == TARGET_A
    assert result.context.target_name == "Luigi's"
    assert result.context.reviews_considered == 1  # the reviews the model saw
    assert result.context.reviews_total == 40
    assert result.context.is_subset is True


def test_the_tool_steps_are_returned_for_the_ui():
    steps = [ToolStep(tool="count_reviews", summary="1 of 3 reviews")]
    result = answer(reply("ANSWERED", cited=["R1"]), steps=steps)

    assert result.steps == steps


def test_a_target_without_reviews_gets_no_reviews_and_nothing_else():
    result = no_reviews_answer(build_bundle(TARGET, {"review_count": 0}))

    assert result.status is AnswerStatus.NO_REVIEWS
    assert result.evidence == [] and result.steps == []
    assert result.refusal_reason is None
    assert result.suggestions == []
    assert result.context.reviews_total == 0


# --- Refusals: reasons and suggestions (S2-012, S2-014, S2-015, S2-BR-021) ---


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("INSUFFICIENT_EVIDENCE", AnswerStatus.INSUFFICIENT_EVIDENCE),
        ("OUT_OF_SCOPE", AnswerStatus.OUT_OF_SCOPE),
    ],
)
def test_a_refusal_keeps_the_explanation_and_never_carries_evidence(status, expected):
    result = answer(reply(status, "  I can only discuss these reviews.  ", ["R1"]))

    assert result.status is expected
    assert result.answer == "I can only discuss these reviews."
    assert result.evidence == []


@pytest.mark.parametrize(
    ("status", "fallback"),
    [
        ("INSUFFICIENT_EVIDENCE", INSUFFICIENT_EVIDENCE_MESSAGE),
        ("OUT_OF_SCOPE", OUT_OF_SCOPE_MESSAGE),
    ],
)
def test_a_refusal_without_text_gets_a_fixed_message(status, fallback):
    assert answer(reply(status, "")).answer == fallback


def test_an_answer_has_no_refusal_reason_or_suggestions():
    result = answer(
        reply(
            "ANSWERED",
            "People like the food.",
            ["R1"],
            refusal_reason="OTHER_ENTITY",
            suggestions=["Ask about the fries?"],
        )
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
    result = answer(refusal("OUT_OF_SCOPE", reason))

    assert result.status is AnswerStatus.OUT_OF_SCOPE
    assert result.refusal_reason is reason


@pytest.mark.parametrize(
    "reason", [None, RefusalReason.NO_EVIDENCE], ids=["no-reason", "evidence-reason"]
)
def test_out_of_scope_without_a_matching_reason_still_refuses(reason):
    # The refusal stands; only the reason is left out (no crash, no answer).
    result = answer(refusal("OUT_OF_SCOPE", reason))

    assert result.status is AnswerStatus.OUT_OF_SCOPE
    assert result.refusal_reason is None


@pytest.mark.parametrize("reason", [None, RefusalReason.OTHER_ENTITY])
def test_insufficient_evidence_always_has_reason_no_evidence(reason):
    result = answer(refusal("INSUFFICIENT_EVIDENCE", reason))

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
    assert result.refusal_reason is RefusalReason.NO_EVIDENCE


def test_suggestions_are_trimmed_deduplicated_and_capped():
    result = answer(
        refusal(
            "OUT_OF_SCOPE",
            RefusalReason.OTHER_PLATFORM,
            ["  What's the rating?  ", "", "What's the rating?", "Fries?", "Staff?"],
        )
    )

    assert result.suggestions == ["What's the rating?", "Fries?"]
    assert len(result.suggestions) == MAX_SUGGESTIONS


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (
            "OUT_OF_SCOPE",
            RefusalReason.GENERAL_KNOWLEDGE,
            "I can only answer questions about the Google Maps reviews for "
            "Luigi's, so I can't help with that.",
        ),
        (
            "OUT_OF_SCOPE",
            RefusalReason.OTHER_ENTITY,
            "I only have reviews for Luigi's, so I can't speak to other "
            "businesses, products or people.",
        ),
        (
            "OUT_OF_SCOPE",
            RefusalReason.OTHER_LOCATION,
            f"These reviews are for Luigi's at {ADDRESS} only, not other locations.",
        ),
        (
            "OUT_OF_SCOPE",
            RefusalReason.OTHER_PLATFORM,
            "I only have Google Maps reviews for Luigi's, not reviews from other "
            "sites.",
        ),
        (
            "INSUFFICIENT_EVIDENCE",
            RefusalReason.NO_EVIDENCE,
            INSUFFICIENT_EVIDENCE_MESSAGE,
        ),
    ],
)
def test_a_blank_refusal_gets_the_template_for_its_reason(status, reason, expected):
    result = answer(reply(status, "", refusal_reason=reason), address=ADDRESS)

    assert result.answer == expected
    assert result.refusal_reason is reason


def test_the_location_template_works_without_an_address():
    result = answer(reply("OUT_OF_SCOPE", "", refusal_reason="OTHER_LOCATION"))

    assert result.answer == "These reviews are for Luigi's only, not other locations."


def test_a_brief_refusal_from_the_model_is_kept():
    text = "I only have Google Maps reviews for Luigi's, not Yelp."
    result = answer(reply("OUT_OF_SCOPE", text, refusal_reason="OTHER_PLATFORM"))

    assert result.answer == text


def test_a_refusal_that_runs_long_is_replaced_by_the_template():
    # A long refusal has most likely answered the question anyway.
    leaky = (
        "I can't say from the reviews, but generally " + "x" * MAX_REFUSAL_CHARACTERS
    )
    result = answer(reply("OUT_OF_SCOPE", leaky, refusal_reason="GENERAL_KNOWLEDGE"))

    assert result.answer.startswith("I can only answer questions about")
    assert "generally" not in result.answer


def test_a_refusal_at_the_length_limit_is_kept():
    text = "x" * MAX_REFUSAL_CHARACTERS
    result = answer(reply("OUT_OF_SCOPE", text, refusal_reason="OTHER_ENTITY"))

    assert result.answer == text


@pytest.mark.parametrize(
    ("status", "reason", "suggestion"),
    [
        ("OUT_OF_SCOPE", RefusalReason.GENERAL_KNOWLEDGE,
         "What do reviewers like most?"),
        ("OUT_OF_SCOPE", RefusalReason.OTHER_ENTITY,
         "What do reviewers like most here?"),
        ("OUT_OF_SCOPE", RefusalReason.OTHER_LOCATION,
         "What do reviewers say about the service here?"),
        ("OUT_OF_SCOPE", RefusalReason.OTHER_PLATFORM,
         "What's the overall rating on Google Maps?"),
        ("INSUFFICIENT_EVIDENCE", None, "What do reviewers mention most often?"),
    ],
)  # fmt: skip
def test_a_refusal_without_suggestions_gets_one_for_its_reason(
    status, reason, suggestion
):
    result = answer(reply(status, "No.", refusal_reason=reason))

    assert result.suggestions == [suggestion]


def test_the_models_own_suggestions_win_over_the_fallback():
    result = answer(
        refusal("OUT_OF_SCOPE", RefusalReason.OTHER_PLATFORM, ["How is the pasta?"])
    )

    assert result.suggestions == ["How is the pasta?"]


def test_an_out_of_scope_refusal_without_a_reason_gets_no_fallback_suggestion():
    # Without a reason there is no safe suggestion to make; the general message stays.
    result = answer(reply("OUT_OF_SCOPE", ""))

    assert result.answer == OUT_OF_SCOPE_MESSAGE
    assert result.suggestions == []


# --- The submit_answer tool the model is offered ---


def test_submit_answer_spec_sends_one_line_and_every_field():
    spec = submit_answer_spec()

    assert spec["name"] == "submit_answer"
    assert spec["description"] == (
        "Finish the question: give your answer, or say why you cannot answer it."
    )
    assert set(spec["parameters"]["properties"]) == {
        "status",
        "answer",
        "cited",
        "refusal_reason",
        "suggestions",
    }
    # The developer notes in SubmitAnswer's docstring are not sent to the model.
    assert "description" not in spec["parameters"]


def test_the_model_cannot_choose_no_reviews():
    # NO_REVIEWS is decided by the app before any model call (S2-BR-025).
    with pytest.raises(ValidationError):
        SubmitAnswer.model_validate({"status": "NO_REVIEWS", "answer": "None."})


# --- The answer text the user reads (plain, without labels) ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Prices are good (R4, R16, R19).", "Prices are good."),
        (
            "Service (R8, R12 and R13) is the top complaint.",
            "Service is the top complaint.",
        ),
        ("One reviewer (e.g. R3) liked it.", "One reviewer liked it."),
        ("Staff are friendly (r2).", "Staff are friendly."),
        ("**What people like:** the crust.", "What people like: the crust."),
        ("Rated 4 stars (out of 5).", "Rated 4 stars (out of 5)."),  # not labels
        ("  Plain already.  ", "Plain already."),
    ],
)
def test_tidy_answer_removes_label_lists_and_bold_markers(text, expected):
    assert tidy_answer(text) == expected


def test_an_answer_reaches_the_user_without_labels_but_keeps_its_evidence():
    result = answer(reply("ANSWERED", "**Mixed views** (R1, R3).", cited=["R1", "R3"]))

    assert result.answer == "Mixed views."
    assert [e.id for e in result.evidence] == [A_ROWS[0]["id"], A_ROWS[2]["id"]]


def test_a_refusal_is_tidied_too():
    result = answer(
        reply(
            "OUT_OF_SCOPE",
            "Only Google Maps reviews (R2).",
            refusal_reason="OTHER_PLATFORM",
        )
    )

    assert result.answer == "Only Google Maps reviews."


def test_an_answer_that_was_only_labels_becomes_insufficient_evidence():
    result = answer(reply("ANSWERED", "(R1, R2)", cited=["R1"]))

    assert result.status is AnswerStatus.INSUFFICIENT_EVIDENCE
