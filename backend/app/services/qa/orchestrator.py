"""S2-010: answer one question about the active AnalysisTarget.

    1. clean the question              blank or too long -> InvalidQuestionError (422)
    2. load the target the caller owns another user's     -> NotFoundError (404)
    3. read the summary (S2-008)       no reviews         -> NoReviewsError (409)
    4. build the messages              rules + briefing, then the question
    5. make the model, run the loop    provider failure   -> QAUnavailableError (503)
                                       wrong-target row   -> QAContextError (500)
    6. build the answer                cited labels -> reviews from the database

Steps 1 to 3 run before the model is made or called; when one stops the request,
nothing is sent to the model. Every call makes a new ToolSet and LoopState and
reads the reviews again, so changing the active target changes the context and
nothing carries over (S2-BR-023, S2-BR-024).

Full mode (QAConfig.mode = "full") is the fallback: every review is sent in the
user message and the model is offered only submit_answer. It is used only while
the target's review text fits full_mode_max_characters; a larger target uses tools.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.core.errors import AppError, ConflictError
from app.llm import ChatModel, ModelError
from app.repositories.review_search import ReviewSearchRepository
from app.schemas.qa import AnswerResponse
from app.services.qa.agent import run
from app.services.qa.answer import build_answer
from app.services.qa.prompt import (
    build_system_prompt,
    clean_question,
    format_all_reviews,
)
from app.services.qa.tools.guard import ToolScopeError, require_in_scope
from app.services.qa.tools.toolset import ToolSet

logger = logging.getLogger(__name__)

# Full mode: each review also costs a few tokens of label and markup.
REVIEW_MARKUP_CHARACTERS = 100


class NoReviewsError(ConflictError):
    """The target has no saved reviews, so there is nothing to answer from."""

    code = "QA_NO_REVIEWS"
    message = (
        "This target has no ingested reviews yet. "
        "Run an ingestion before asking questions about it."
    )


class QAUnavailableError(AppError):
    """The model failed or is not configured. Provider text is logged by type only
    and never reaches the user."""

    status_code = 503
    code = "QA_UNAVAILABLE"
    message = "ReviewLens couldn't answer right now. Please try again in a moment."


class QAContextError(AppError):
    """A review from outside the active target reached a tool. Nothing more is sent."""

    status_code = 500
    code = "QA_CONTEXT_ERROR"
    message = "We couldn't prepare the reviews for this question. Please try again."


@dataclass(frozen=True)
class QAConfig:
    max_tool_calls: int = 6
    timeout_seconds: float = 30
    mode: Literal["tools", "full"] = "tools"
    full_mode_max_characters: int = 120_000

    @classmethod
    def from_settings(cls, settings) -> "QAConfig":
        return cls(
            max_tool_calls=settings.qa_max_tool_calls,
            timeout_seconds=settings.qa_timeout_seconds,
            mode=settings.qa_mode,
            full_mode_max_characters=settings.qa_full_mode_max_characters,
        )


class TargetSource(Protocol):
    def get_owned(self, target_id: UUID | str, owner_id: UUID | str) -> dict: ...


class ReviewSource(Protocol):
    """The ReviewRepository methods Q&A uses."""

    def summary_for_target(
        self, target_id: UUID | str, owner_id: UUID | str
    ) -> dict: ...

    def list_for_target(
        self, target_id: UUID | str, owner_id: UUID | str
    ) -> list[dict]: ...


def answer_question(
    *,
    owner_id: UUID | str,
    target_id: UUID | str,
    question: str,
    targets: TargetSource,
    reviews: ReviewSource,
    search: ReviewSearchRepository,
    make_model: Callable[[], ChatModel],
    config: QAConfig | None = None,
    make_toolset: Callable[..., ToolSet] = ToolSet,
) -> AnswerResponse:
    config = config or QAConfig()
    question = clean_question(question)
    target = targets.get_owned(target_id, owner_id)
    summary = reviews.summary_for_target(target["id"], owner_id)
    total = summary["review_count"]
    if total == 0:
        raise NoReviewsError()

    toolset = make_toolset(search, owner_id, target["id"])
    rows = _full_mode_rows(reviews, target, owner_id, config)
    system = build_system_prompt(
        target,
        summary,
        max_tool_calls=config.max_tool_calls,
        full_mode=rows is not None,
    )
    messages: list[BaseMessage] = [SystemMessage(system)]
    if rows is not None:
        listing = format_all_reviews(toolset.labels, rows)
        messages.append(HumanMessage(f"{listing}\n\nQuestion: {question}"))
    else:
        messages.append(HumanMessage(question))

    try:
        submitted, state = run(
            make_model(),
            toolset,
            messages,
            max_tool_calls=config.max_tool_calls,
            timeout_s=config.timeout_seconds,
            offer_tools=rows is None,
        )
    except ModelError as exc:
        logger.warning(
            "Q&A model call failed for target %s (%s).",
            target["id"],
            type(exc).__name__,
        )
        raise QAUnavailableError() from exc
    except ToolScopeError as exc:
        logger.error(
            "Q&A tool for target %s returned a review of another target; "
            "nothing more was sent to the model.",
            target["id"],
        )
        raise QAContextError() from exc

    if rows is not None:
        state.shown_review_ids.update(row["id"] for row in rows)
    return build_answer(
        submitted,
        target=target,
        owner_id=owner_id,
        reviews_total=total,
        labels=toolset.labels,
        search=search,
        shown_review_ids=state.shown_review_ids,
        steps=state.steps,
    )


def _full_mode_rows(
    reviews: ReviewSource, target: dict, owner_id: UUID | str, config: QAConfig
) -> list[dict] | None:
    """Every review of the target for full mode, or None to use tools mode: when
    tools mode is set, or the reviews don't fit full_mode_max_characters."""
    if config.mode != "full":
        return None
    rows = reviews.list_for_target(target["id"], owner_id)
    require_in_scope(rows, target["id"])
    size = sum(len(row["review_text"]) + REVIEW_MARKUP_CHARACTERS for row in rows)
    if size > config.full_mode_max_characters:
        logger.info(
            "Target %s is too large for full mode (%d characters); using tools.",
            target["id"],
            size,
        )
        return None
    return rows
