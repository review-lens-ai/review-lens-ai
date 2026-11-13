"""SubmitAnswer: the model's final tool call (submit_answer).

The Q&A loop binds SubmitAnswer as a tool, so these tests check what the model
will be offered (the reasons and their descriptions) and what arguments it may
send back.
"""

import pytest
from pydantic import ValidationError

from app.llm import ReplyStatus
from app.schemas.qa import RefusalReason
from app.services.qa.answer import MAX_CITATIONS, MAX_SUGGESTIONS, SubmitAnswer


def test_an_answer_needs_only_a_status_and_text():
    submitted = SubmitAnswer(status="ANSWERED", answer="People like the fries.")

    assert submitted.status is ReplyStatus.ANSWERED
    assert submitted.cited == []
    assert submitted.refusal_reason is None
    assert submitted.suggestions == []


def test_a_refusal_carries_its_reason_and_suggestions():
    submitted = SubmitAnswer(
        status="OUT_OF_SCOPE",
        answer="I only have Google Maps reviews, not Yelp.",
        refusal_reason="OTHER_PLATFORM",
        suggestions=["What's the overall rating on Google Maps?"],
    )

    assert submitted.refusal_reason is RefusalReason.OTHER_PLATFORM
    assert submitted.suggestions == ["What's the overall rating on Google Maps?"]


def test_there_are_five_refusal_reasons():
    assert {reason.value for reason in RefusalReason} == {
        "GENERAL_KNOWLEDGE",
        "OTHER_ENTITY",
        "OTHER_LOCATION",
        "OTHER_PLATFORM",
        "NO_EVIDENCE",
    }


@pytest.mark.parametrize(
    "args",
    [
        {"status": "MAYBE", "answer": "x"},  # not a status
        {"status": "OUT_OF_SCOPE", "answer": "x", "refusal_reason": "RUDE"},
        {"status": "ANSWERED"},  # no answer text
    ],
    ids=["unknown-status", "unknown-reason", "missing-answer"],
)
def test_arguments_outside_the_schema_are_rejected(args):
    with pytest.raises(ValidationError):
        SubmitAnswer(**args)


def test_a_mismatched_status_and_reason_is_accepted_for_the_builder_to_fix():
    # Rejecting it here would leave the model retrying; the answer builder corrects it.
    submitted = SubmitAnswer(
        status="ANSWERED", answer="x", refusal_reason="NO_EVIDENCE"
    )

    assert submitted.refusal_reason is RefusalReason.NO_EVIDENCE


def test_the_tool_schema_tells_the_model_every_reason_and_field():
    # This JSON schema is what LangChain shows the model for submit_answer.
    schema = SubmitAnswer.model_json_schema()
    text = str(schema)

    assert set(schema["properties"]) == {
        "status",
        "answer",
        "cited",
        "refusal_reason",
        "suggestions",
    }
    assert schema["required"] == ["status", "answer"]
    for reason in RefusalReason:
        assert reason.value in text
    for field in schema["properties"].values():
        assert field.get("description")
    assert str(MAX_CITATIONS) in schema["properties"]["cited"]["description"]
    assert str(MAX_SUGGESTIONS) in schema["properties"]["suggestions"]["description"]
