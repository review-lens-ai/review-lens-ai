"""S2-010: the tool loop for one question.

    submitted, state = run(model, toolset, messages, max_tool_calls=6, timeout_s=30)

Our code runs the loop; the model only asks. Each turn the model sees every message
so far and replies with tool calls or a final submit_answer:

    model ──tool call──► toolset.registry.run() ──result──► ToolMessage ──► model ...
    model ──submit_answer(status, answer, cited)──► returned to the orchestrator

Rules:
- THE MODEL DECIDES WHEN IT HAS ENOUGH, within a budget: every tool result ends with
  how many tool calls are left. Once the budget or the time is used up, the model is
  offered only submit_answer. A model that still asks for a tool is told none are
  left, and the loop stops after max_tool_calls + 2 model calls in all.
- TOOL ERRORS GO BACK TO THE MODEL: an unknown tool or bad arguments come back as a
  ToolResult with is_error, so the model can try again. ToolScopeError (a review
  from outside the active target) is a bug and is not caught: it stops the question.
- REPEATS: the same tool with the same arguments is not run again once it has
  succeeded. A call that failed may be tried again, since it can work later.
- A REPLY WITHOUT A TOOL CALL gets one reminder to call a tool; a second ends the loop.
  An invalid submit_answer gets one chance to be corrected.
- ONE QUESTION ONLY: LoopState is made per call and thrown away after. Nothing is
  kept between questions or shared between users (S2-019).

No reply, a second invalid submit_answer, or the model-call limit returns None,
which build_answer turns into INSUFFICIENT_EVIDENCE.
"""

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from pydantic import ValidationError

from app.llm import ChatModel
from app.schemas.qa import ToolStep
from app.services.qa.answer import SUBMIT_ANSWER, SubmitAnswer, submit_answer_spec
from app.services.qa.tools.registry import ToolRegistry

NUDGE = f"Reply by calling a tool. When you are done, call {SUBMIT_ANSWER}."
NO_CALLS_LEFT = f"No tool calls left. Call {SUBMIT_ANSWER} now with what you have."
MALFORMED_CALL = "That tool call could not be read. Send it again as valid JSON."
STEP_SUMMARY_CHARACTERS = 120


class ToolSource(Protocol):
    """What run() needs from a ToolSet (app/services/qa/tools/toolset.py)."""

    registry: ToolRegistry


@dataclass
class LoopState:
    messages: list[BaseMessage]
    started_at: float
    tool_calls_used: int = 0
    model_calls: int = 0
    steps: list[ToolStep] = field(default_factory=list)
    shown_review_ids: set[UUID] = field(default_factory=set)
    seen: set[str] = field(default_factory=set)
    nudged: bool = False
    bad_submits: int = 0


def run(
    model: ChatModel,
    toolset: ToolSource,
    messages: list[BaseMessage],
    *,
    max_tool_calls: int,
    timeout_s: float,
    offer_tools: bool = True,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[SubmitAnswer | None, LoopState]:
    state = LoopState(messages=list(messages), started_at=clock())
    specs = (toolset.registry.specs() if offer_tools else []) + [submit_answer_spec()]
    budget = max_tool_calls if offer_tools else 0

    while state.model_calls < budget + 2:
        out_of_budget = state.tool_calls_used >= budget
        out_of_time = clock() - state.started_at >= timeout_s
        choice = SUBMIT_ANSWER if out_of_budget or out_of_time else None
        reply = model.bind_tools(specs, tool_choice=choice).invoke(state.messages)
        state.model_calls += 1
        state.messages.append(reply)

        if not reply.tool_calls:
            if reply.invalid_tool_calls:
                for bad in reply.invalid_tool_calls:
                    state.messages.append(
                        ToolMessage(MALFORMED_CALL, tool_call_id=bad["id"] or "invalid")
                    )
                continue
            if state.nudged:
                return None, state
            state.nudged = True
            state.messages.append(HumanMessage(NUDGE))
            continue

        for call in reply.tool_calls:
            if call["name"] == SUBMIT_ANSWER:
                try:
                    return SubmitAnswer.model_validate(call["args"]), state
                except ValidationError as exc:
                    state.bad_submits += 1
                    if state.bad_submits > 1:
                        return None, state
                    text = f"{SUBMIT_ANSWER} was not valid: {_problems(exc)}. Fix it."
            else:
                text = _run_tool(state, toolset.registry, call, budget)
            state.messages.append(ToolMessage(text, tool_call_id=call["id"]))
    return None, state


def _run_tool(state: LoopState, registry: ToolRegistry, call: dict, budget: int) -> str:
    key = json.dumps([call["name"], call["args"]], sort_keys=True, default=str)
    if key in state.seen:
        text = (
            f"You already ran {call['name']} with these arguments; the result is above."
        )
    elif state.tool_calls_used >= budget:
        return NO_CALLS_LEFT
    else:
        state.tool_calls_used += 1
        result = registry.run(call["name"], call["args"])  # ToolScopeError propagates
        if not result.is_error:
            # A failed call may work later (e.g. get_reviews once a label exists),
            # so only a successful one counts as already run.
            state.seen.add(key)
        state.shown_review_ids.update(source.review_id for source in result.sources)
        state.steps.append(ToolStep(tool=call["name"], summary=_summary(result)))
        text = result.text
    left = budget - state.tool_calls_used
    return f"{text}\n({left} tool call{'' if left == 1 else 's'} left)"


def _summary(result) -> str:
    if result.is_error:
        return "error"
    first = result.text.splitlines()[0] if result.text else ""
    if len(first) > STEP_SUMMARY_CHARACTERS:
        first = first[: STEP_SUMMARY_CHARACTERS - 1].rstrip() + "…"
    return first


def _problems(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
        for e in exc.errors()
    )
