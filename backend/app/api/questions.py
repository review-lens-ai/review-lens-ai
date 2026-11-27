"""Review Q&A endpoint (S2-009, S2-010), mounted under /api/v1/targets.

    POST   /api/v1/targets/{target_id}/questions   {"question": "..."}  -> AnswerResponse
    GET    /api/v1/targets/{target_id}/questions?limit=&offset=
                                                   -> QuestionList, oldest first (S3-006)
    DELETE /api/v1/targets/{target_id}/questions   -> 204, clears only the Q&A (S3-006)

The router is mounted on api_router, so every request comes from a verified user.
Ownership comes from the token: another user's target is a 404 before anything is
read or sent to the model (S2-BR-022, S2-BR-026). Only the question is sent; there
is no chat history sent to the model.

Every 200 answer (answered, refused or NO_REVIEWS) is saved with the target, in the
same transaction (S3-BR-009). A request that fails saves nothing.

    200  an answer, a refusal, or NO_REVIEWS for a target without reviews
    404  the target is not the caller's
    422  blank or too long question
    500  a review from another target reached a tool (QAContextError)
    503  the model failed or is not configured (QAUnavailableError)
"""

from collections.abc import Callable
from functools import partial
from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query

from app.auth import OwnerId
from app.core.config import Settings, get_settings
from app.core.errors import error_responses
from app.db import get_connection
from app.llm import ChatModel
from app.repositories.questions import QuestionRepository
from app.repositories.review_search import ReviewSearchRepository
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.qa import AnswerResponse, QuestionCreate, QuestionList
from app.services.embed_reviews import FastEmbedder
from app.services.qa import QAConfig, answer_question
from app.services.qa.prompt import clean_question

router = APIRouter(prefix="/targets", tags=["questions"])

# GET /questions paging: the latest 100 by default (more than a typical history).
QUESTION_PAGE_SIZE = 100
MAX_QUESTION_PAGE_SIZE = 200
MAX_QUESTION_OFFSET = 100_000

# One embedder for the process: the model loads on first use, so a question that
# never searches by meaning pays nothing. It embeds only the phrase the model asks for.
_EMBEDDER = FastEmbedder()


def get_model_factory(
    settings: Annotated[Settings, Depends(get_settings)],
) -> Callable[[], ChatModel]:
    """Makes the model only once the checks before it pass, so a question that is
    blank, about another user's target or about a target without reviews never
    needs the provider. Tests replace this with a fake model."""
    from app.llm.openrouter import make_chat_model  # loads the provider SDK

    return partial(make_chat_model, settings)


Conn = Annotated[psycopg.Connection, Depends(get_connection)]
ModelFactory = Annotated[Callable[[], ChatModel], Depends(get_model_factory)]
AppSettings = Annotated[Settings, Depends(get_settings)]


@router.post(
    "/{target_id}/questions",
    response_model=AnswerResponse,
    responses=error_responses(404, 500, 503),
)
def ask_question(
    target_id: UUID,
    body: QuestionCreate,
    owner_id: OwnerId,
    conn: Conn,
    make_model: ModelFactory,
    settings: AppSettings,
) -> AnswerResponse:
    """S2-009: ask one question about one of the user's targets. The answer is
    built from that target's saved reviews only. S3-006: the answer is saved
    to the target's Q&A history before it is returned."""
    response = answer_question(
        owner_id=owner_id,
        target_id=target_id,
        question=body.question,
        targets=TargetRepository(conn),
        reviews=ReviewRepository(conn),
        open_search=partial(ReviewSearchRepository.for_owner, conn, embedder=_EMBEDDER),
        make_model=make_model,
        config=QAConfig.from_settings(settings),
    )
    # clean_question already passed inside answer_question; this saves the same text.
    QuestionRepository(conn).add(
        target_id, owner_id, clean_question(body.question), response
    )
    return response


@router.get(
    "/{target_id}/questions",
    response_model=QuestionList,
    responses=error_responses(404),
)
def list_questions(
    target_id: UUID,
    owner_id: OwnerId,
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=MAX_QUESTION_PAGE_SIZE)] = QUESTION_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_QUESTION_OFFSET)] = 0,
) -> dict:
    """S3-006, S3-007: one page of the target's saved questions and responses,
    oldest first. Paging counts back from the newest question, like a chat:
    offset 0 is the latest `limit` questions, offset=limit the ones before them.
    total is every saved question, so the caller knows if there are earlier ones.
    Another user's target is a 404, indistinguishable from one that does not exist."""
    questions = QuestionRepository(conn)
    return {
        "items": questions.list_for_target(
            target_id, owner_id, limit=limit, offset=offset
        ),
        "total": questions.count_for_target(target_id, owner_id),
        "limit": limit,
        "offset": offset,
    }


@router.delete(
    "/{target_id}/questions", status_code=204, responses=error_responses(404)
)
def clear_questions(target_id: UUID, owner_id: OwnerId, conn: Conn) -> None:
    """S3-006, S3-008: clear the target's Q&A history. The target, its runs and its
    reviews stay. Another user's target is a 404."""
    QuestionRepository(conn).clear_for_target(target_id, owner_id)
