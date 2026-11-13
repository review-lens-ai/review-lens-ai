"""Analysis target endpoints, mounted under /api/v1/targets.

The router is mounted on api_router, so every endpoint here already requires a
verified user. Ownership always comes from get_current_user_id (the token),
never from the request.
"""

from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query

from app.auth import OwnerId
from app.core.errors import error_responses
from app.db import get_connection
from app.platforms import SUPPORTED_PLATFORM
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewPage, ReviewSummary
from app.schemas.targets import TargetCreate, TargetList, TargetResponse

router = APIRouter(prefix="/targets", tags=["targets"])

# A target can hold thousands of reviews; the browser gets them a page at a time.
REVIEW_PAGE_SIZE = 50
MAX_REVIEW_PAGE_SIZE = 100
# Postgres rejects an OFFSET past bigint (a 500); no UI pages this deep anyway.
MAX_REVIEW_OFFSET = 100_000


def get_target_repository(
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> TargetRepository:
    return TargetRepository(conn)


def get_review_repository(
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> ReviewRepository:
    return ReviewRepository(conn)


Repo = Annotated[TargetRepository, Depends(get_target_repository)]
ReviewRepo = Annotated[ReviewRepository, Depends(get_review_repository)]


@router.get("", response_model=TargetList)
def list_targets(owner_id: OwnerId, repo: Repo) -> dict:
    """S1-017: only the signed-in user's targets. The owner filter runs in SQL
    (TargetRepository.list_for_owner), not in the browser."""
    return {"items": repo.list_for_owner(owner_id)}


@router.get(
    "/{target_id}", response_model=TargetResponse, responses=error_responses(404)
)
def get_target(target_id: UUID, owner_id: OwnerId, repo: Repo) -> dict:
    """S1-018: one target for its workspace. Another user's target is a 404,
    indistinguishable from one that does not exist."""
    return repo.get_owned(target_id, owner_id)


@router.get(
    "/{target_id}/reviews", response_model=ReviewPage, responses=error_responses(404)
)
def list_target_reviews(
    target_id: UUID,
    owner_id: OwnerId,
    reviews: ReviewRepo,
    limit: Annotated[int, Query(ge=1, le=MAX_REVIEW_PAGE_SIZE)] = REVIEW_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0, le=MAX_REVIEW_OFFSET)] = 0,
) -> dict:
    """S1-022: one page of a target's saved reviews, through its owner only.
    Another user's target is a 404, indistinguishable from one that does not exist.

    Known limits, accepted for now:
    - items and total are two queries. A run that commits between them makes
      total count reviews the page did not see.
    - A run that finishes while someone pages shifts the rows, so the next page
      can repeat a few reviews. The UI goes back to page 1 when a run finishes.
    - Future: one repository method that checks the owner once and returns
      (rows, total) would save a round trip and could read both in one snapshot.
    """
    return {
        "items": reviews.list_for_target(
            target_id, owner_id, limit=limit, offset=offset
        ),
        "total": reviews.count_for_target(target_id, owner_id),
        "limit": limit,
        "offset": offset,
    }


@router.get(
    "/{target_id}/summary", response_model=ReviewSummary, responses=error_responses(404)
)
def get_target_summary(target_id: UUID, owner_id: OwnerId, reviews: ReviewRepo) -> dict:
    """S2-008: review count, average rating and rating counts for one of the
    user's targets, calculated from its saved reviews (the same dataset Q&A uses).
    Another user's target is a 404, indistinguishable from one that does not exist.
    """
    return reviews.summary_for_target(target_id, owner_id)


@router.delete("/{target_id}", status_code=204, responses=error_responses(404))
def delete_target(target_id: UUID, owner_id: OwnerId, repo: Repo) -> None:
    """S1-012: delete one of your own targets. Another user's target is a 404."""
    repo.delete_owned(target_id, owner_id)


@router.post("", status_code=201, response_model=TargetResponse)
def create_target(payload: TargetCreate, owner_id: OwnerId, repo: Repo) -> dict:
    """S1-015: create a target owned by the signed-in user."""
    return repo.create(owner_id, payload.name, SUPPORTED_PLATFORM, payload.source_url)
