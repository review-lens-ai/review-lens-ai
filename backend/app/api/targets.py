"""Analysis target endpoints, mounted under /api/v1/targets.

The router is mounted on api_router, so every endpoint here already requires a
verified user. Ownership always comes from get_current_user_id (the token),
never from the request.
"""

from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends

from app.auth import get_current_user_id
from app.db import get_connection
from app.platforms import SUPPORTED_PLATFORM
from app.repositories.reviews import ReviewRepository
from app.repositories.targets import TargetRepository
from app.schemas.reviews import ReviewList
from app.schemas.targets import TargetCreate, TargetList, TargetResponse

router = APIRouter(prefix="/targets", tags=["targets"])


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
OwnerId = Annotated[str, Depends(get_current_user_id)]


@router.get("", response_model=TargetList)
def list_targets(owner_id: OwnerId, repo: Repo) -> dict:
    """S1-017: only the signed-in user's targets. The owner filter runs in SQL
    (TargetRepository.list_for_owner), not in the browser."""
    return {"items": repo.list_for_owner(owner_id)}


@router.get("/{target_id}", response_model=TargetResponse)
def get_target(target_id: UUID, owner_id: OwnerId, repo: Repo) -> dict:
    """S1-018: one target for its workspace. Another user's target is a 404,
    indistinguishable from one that does not exist."""
    return repo.get_owned(target_id, owner_id)


@router.get("/{target_id}/reviews", response_model=ReviewList)
def list_target_reviews(
    target_id: UUID, owner_id: OwnerId, reviews: ReviewRepo
) -> dict:
    """S1-022: a target's saved reviews, through its owner only. Another user's
    target is a 404, indistinguishable from one that does not exist."""
    return {"items": reviews.list_for_target(target_id, owner_id)}


@router.delete("/{target_id}", status_code=204)
def delete_target(target_id: UUID, owner_id: OwnerId, repo: Repo) -> None:
    """S1-012: delete one of your own targets. Another user's target is a 404."""
    repo.delete_owned(target_id, owner_id)


@router.post("", status_code=201, response_model=TargetResponse)
def create_target(payload: TargetCreate, owner_id: OwnerId, repo: Repo) -> dict:
    """S1-015: create a target owned by the signed-in user."""
    return repo.create(owner_id, payload.name, SUPPORTED_PLATFORM, payload.source_url)
