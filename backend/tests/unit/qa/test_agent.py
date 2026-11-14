"""S2-010: the tool loop's rules, with the fake model and the in-memory tools.

Each test scripts the model's turns and checks what the loop sent back to it:
tool results with the calls left, errors the model can recover from, the forced
finish, the repeat guard, and the limits that always end the loop.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

import app.services.qa as qa
from app.schemas.qa import AnswerStatus
from app.services.qa.agent import (
    MALFORMED_CALL,
    NO_CALLS_LEFT,
    NUDGE,
    STEP_SUMMARY_CHARACTERS,
    _summary,
    run,
)
from app.services.qa.tools.guard import ToolScopeError
from app.services.qa.tools.types import ToolResult
from tests.unit.qa.conftest import (
    OWNER_A,
    TARGET_A,
    TARGET_B,
    FakeChatModel,
    call,
    fake_toolset,
    submit,
)

QUESTION = [SystemMessage("rules"), HumanMessage("What do people say?")]


def loop(model, *, max_tool_calls=6, timeout_s=30, toolset=None, **kwargs):
    toolset = toolset or fake_toolset(None, OWNER_A, TARGET_A)
    return run(
        model,
        toolset,
        QUESTION,
        max_tool_calls=max_tool_calls,
        timeout_s=timeout_s,
        **kwargs,
    )


def tool_replies(model) -> list[str]:
    """The ToolMessage texts the loop sent, in order, from the last call."""
    return [m.content for m in model.last["messages"] if isinstance(m, ToolMessage)]


def test_a_tool_result_goes_back_to_the_model_with_the_calls_left():
    model = FakeChatModel(call("count_reviews", words="slow"), submit("ANSWERED"))
    submitted, state = loop(model)

    assert submitted.status is AnswerStatus.ANSWERED
    assert tool_replies(model) == ["1 of 3 reviews\n(5 tool calls left)"]
    assert state.tool_calls_used == 1
    assert state.model_calls == 2


def test_the_model_is_offered_every_tool_and_submit_answer():
    model = FakeChatModel(submit("OUT_OF_SCOPE", "Not about these reviews."))
    loop(model)

    assert model.last["tools"] == ["find_reviews", "count_reviews", "submit_answer"]
    assert model.last["tool_choice"] is None


def test_the_question_messages_are_sent_first_and_not_changed():
    model = FakeChatModel(submit("OUT_OF_SCOPE", "No."))
    loop(model)

    assert model.calls[0]["messages"] == QUESTION


def test_steps_and_shown_reviews_are_recorded():
    model = FakeChatModel(
        call("find_reviews", words="food"),
        call("count_reviews"),
        submit("ANSWERED"),
    )
    _, state = loop(model)

    assert [s.tool for s in state.steps] == ["find_reviews", "count_reviews"]
    assert state.steps[1].summary == "3 of 3 reviews"
    assert len(state.shown_review_ids) == 1  # "Great food, a bit loud on weekends."


def test_an_unknown_tool_is_an_error_the_model_can_read():
    model = FakeChatModel(call("drop_table"), submit("OUT_OF_SCOPE", "No."))
    _, state = loop(model)

    [reply] = tool_replies(model)
    assert reply.startswith("Unknown tool 'drop_table'")
    assert "find_reviews" in reply
    assert state.steps[0].summary == "error"
    assert state.tool_calls_used == 1  # errors use the budget too


def test_bad_arguments_are_an_error_the_model_can_read():
    model = FakeChatModel(call("count_reviews", stars=9), submit("OUT_OF_SCOPE", "No."))
    loop(model)

    [reply] = tool_replies(model)
    assert reply.startswith("Bad arguments for 'count_reviews'")


def test_the_same_call_twice_runs_once():
    toolset = fake_toolset(None, OWNER_A, TARGET_A)
    model = FakeChatModel(
        call("count_reviews", words="slow"),
        call("count_reviews", words="slow"),
        submit("ANSWERED"),
    )
    _, state = loop(model, toolset=toolset)

    assert toolset.runs == [("count_reviews", "slow")]
    assert "already ran count_reviews" in tool_replies(model)[1]
    assert state.tool_calls_used == 1


def test_a_call_that_failed_can_be_tried_again():
    model = FakeChatModel(
        call("count_reviews", stars=9),
        call("count_reviews", stars=9),
        submit("OUT_OF_SCOPE", "No."),
    )
    _, state = loop(model)

    first, second = tool_replies(model)
    assert first.startswith("Bad arguments") and second.startswith("Bad arguments")
    assert state.tool_calls_used == 2


def test_after_the_budget_only_submit_answer_is_allowed():
    model = FakeChatModel(
        call("count_reviews", words="a"),
        call("count_reviews", words="b"),
        submit("ANSWERED"),
    )
    loop(model, max_tool_calls=2)

    assert [c["tool_choice"] for c in model.calls] == [None, None, "submit_answer"]
    assert tool_replies(model)[1].endswith("(0 tool calls left)")


def test_one_call_left_is_singular():
    model = FakeChatModel(call("count_reviews"), submit("ANSWERED"))
    loop(model, max_tool_calls=2)

    assert tool_replies(model)[0].endswith("(1 tool call left)")


def test_a_tool_asked_for_after_the_budget_is_not_run():
    toolset = fake_toolset(None, OWNER_A, TARGET_A)
    model = FakeChatModel(
        call("count_reviews", words="a"),
        call("count_reviews", words="b"),  # ignores the forced submit_answer
        submit("ANSWERED"),
    )
    loop(model, max_tool_calls=1, toolset=toolset)

    assert toolset.runs == [("count_reviews", "a")]
    assert tool_replies(model)[1] == NO_CALLS_LEFT


def test_after_the_time_limit_only_submit_answer_is_allowed():
    times = iter([0, 0, 31, 31])  # start, first call, second call
    model = FakeChatModel(call("count_reviews"), submit("ANSWERED"))
    loop(model, timeout_s=30, clock=lambda: next(times))

    assert [c["tool_choice"] for c in model.calls] == [None, "submit_answer"]


def test_the_loop_always_ends_after_the_model_call_limit():
    model = FakeChatModel(*[call("count_reviews", words=str(i)) for i in range(10)])
    submitted, state = loop(model, max_tool_calls=2)

    assert submitted is None
    assert state.model_calls == 4  # max_tool_calls + 2


def test_a_review_from_another_target_stops_the_question():
    toolset = fake_toolset(None, OWNER_A, TARGET_A, leak_from=TARGET_B)
    model = FakeChatModel(call("find_reviews"), submit("ANSWERED"))

    with pytest.raises(ToolScopeError):
        loop(model, toolset=toolset)
    assert len(model.calls) == 1  # nothing more was sent


def test_an_invalid_submit_answer_gets_one_chance():
    model = FakeChatModel(
        call("submit_answer", status="MAYBE", answer="Hmm."),
        submit("OUT_OF_SCOPE", "Not about these reviews."),
    )
    submitted, _ = loop(model)

    assert submitted.status is AnswerStatus.OUT_OF_SCOPE
    assert tool_replies(model)[0].startswith("submit_answer was not valid: status")


def test_a_second_invalid_submit_answer_ends_the_loop():
    model = FakeChatModel(
        call("submit_answer", status="MAYBE", answer="Hmm."),
        call("submit_answer", answer="no status"),
    )
    submitted, _ = loop(model)

    assert submitted is None


def test_plain_text_gets_one_reminder_to_call_a_tool():
    model = FakeChatModel(AIMessage("Here is my answer."), submit("ANSWERED"))
    submitted, state = loop(model)

    assert submitted is not None
    assert state.messages[-2] == HumanMessage(NUDGE)  # before the final reply


def test_plain_text_twice_ends_the_loop():
    model = FakeChatModel(AIMessage("One."), AIMessage("Two."))
    submitted, _ = loop(model)

    assert submitted is None


def test_a_malformed_tool_call_is_answered_and_the_loop_goes_on():
    broken = AIMessage(
        "",
        invalid_tool_calls=[
            {"name": "count_reviews", "args": "{oops", "id": "bad1", "error": None}
        ],
    )
    model = FakeChatModel(broken, submit("ANSWERED"))
    submitted, _ = loop(model)

    assert submitted is not None
    assert tool_replies(model) == [MALFORMED_CALL]


def test_without_tools_only_submit_answer_is_offered_and_required():
    model = FakeChatModel(submit("ANSWERED"))
    loop(model, offer_tools=False)

    assert model.last["tools"] == ["submit_answer"]
    assert model.last["tool_choice"] == "submit_answer"


def test_nothing_is_kept_between_questions():
    toolset = fake_toolset(None, OWNER_A, TARGET_A)
    first = FakeChatModel(call("count_reviews"), submit("ANSWERED"))
    loop(first, toolset=toolset)
    second = FakeChatModel(submit("ANSWERED"))
    _, state = loop(second)

    assert second.calls[0]["messages"] == QUESTION
    assert state.steps == []


def test_a_long_first_line_is_shortened_in_the_step_summary():
    summary = _summary(ToolResult(text="x" * 500 + "\nsecond line"))

    assert len(summary) == STEP_SUMMARY_CHARACTERS
    assert summary.endswith("…")


def test_the_package_only_exports_what_it_lists():
    assert qa.answer_question.__name__ == "answer_question"
    with pytest.raises(AttributeError):
        qa.not_a_name  # noqa: B018
