"""S2-010: answer one question about the active AnalysisTarget.

    1. clean the question              blank or too long -> InvalidQuestionError (422)
    2. load the target the caller owns another user's     -> NotFoundError (404)
    3. read the summary (S2-008)       no reviews         -> NO_REVIEWS answer
    4. build the messages              scope prompt + mode, then the question
    5. make the model, run the loop    provider failure   -> QAUnavailableError (503)
                                       wrong-target row   -> QAContextError (500)
    6. build the answer                cited labels -> reviews from the database

Steps 1 to 3 run before the model is made or called; when one ends the request,
nothing is sent to the model. Every call opens a new owner-checked search
(ReviewSearchRepository.for_owner), ToolSet and LoopState and reads the reviews
again, so changing the active target changes the context and nothing carries over
(S2-BR-023, S2-BR-024).

Full mode (QAConfig.mode = "full") is the fallback: every review is sent in the
user message and the model is offered only submit_answer. It is used only while
the target's review text fits full_mode_max_characters; a larger target uses tools.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage

from app.core.errors import AppError
from app.llm import ChatModel, ModelError
from app.repositories.review_search import ReviewSearchRepository
from app.schemas.qa import AnswerResponse
from app.services.qa.agent import run
from app.services.qa.answer import LoopRecord, build_answer, no_reviews_answer
from app.services.qa.context import build_bundle
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

    def place_address(
        self, target_id: UUID | str, owner_id: UUID | str
    ) -> str | None: ...


class ReviewSource(Protocol):
    """The ReviewRepository methods Q&A uses."""

    def summary_for_target(
        self, target_id: UUID | str, owner_id: UUID | str
    ) -> dict: ...

    def list_for_target(
        self, target_id: UUID | str, owner_id: UUID | str
    ) -> list[dict]: ...


# (target_id, owner_id) -> the owner-checked search for that target, usually
# functools.partial(ReviewSearchRepository.for_owner, conn).
SearchFactory = Callable[[UUID | str, UUID | str], ReviewSearchRepository]


def answer_question(
    *,
    owner_id: UUID | str,
    target_id: UUID | str,
    question: str,
    targets: TargetSource,
    reviews: ReviewSource,
    open_search: SearchFactory,
    make_model: Callable[[], ChatModel],
    config: QAConfig | None = None,
    make_toolset: Callable[[ReviewSearchRepository], ToolSet] = ToolSet,
) -> AnswerResponse:
    config = config or QAConfig()
    question = clean_question(question)

    target = targets.get_owned(target_id, owner_id)
    summary = reviews.summary_for_target(target["id"], owner_id)
    if summary["review_count"] == 0:
        return no_reviews_answer(build_bundle(target, summary))
    bundle = build_bundle(
        target, summary, address=targets.place_address(target["id"], owner_id)
    )

    search = open_search(target["id"], owner_id)
    toolset = make_toolset(search)
    rows = _full_mode_rows(reviews, target, owner_id, config)
    if rows is not None:
        system = build_system_prompt(bundle, full_mode_reviews=len(rows))
        listing = format_all_reviews(toolset.labels, rows)
        user = HumanMessage(f"{listing}\n\nQuestion: {question}")
    else:
        system = build_system_prompt(bundle, max_tool_calls=config.max_tool_calls)
        user = HumanMessage(question)
    messages: list[BaseMessage] = [SystemMessage(system), user]

    started = time.perf_counter()
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
        # Type names only: the provider's text can hold keys and never reaches a log.
        cause = type(exc.__cause__).__name__ if exc.__cause__ else "no cause"
        logger.warning(
            "Q&A model call failed for target %s (%s, caused by %s) after %.1fs.",
            target["id"],
            type(exc).__name__,
            cause,
            time.perf_counter() - started,
        )
        raise QAUnavailableError() from exc
    except ToolScopeError as exc:
        logger.error(
            "Q&A tool for target %s returned a review of another target; "
            "nothing more was sent to the model.",
            target["id"],
        )
        raise QAContextError() from exc

    logger.info(
        "Q&A for target %s: %d model calls, %d tool calls, %.1fs.",
        target["id"],
        state.model_calls,
        state.tool_calls_used,
        time.perf_counter() - started,
    )
    shown = set(state.shown_review_ids)
    if rows is not None:
        shown.update(row["id"] for row in rows)
    return build_answer(
        submitted,
        bundle=bundle,
        labels=toolset.labels,
        search=search,
        record=LoopRecord(steps=tuple(state.steps), shown_review_ids=frozenset(shown)),
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
