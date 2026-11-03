"""Analysis target endpoints, mounted under /api/v1/targets.

The router is mounted on api_router, so every endpoint here already requires a
verified user. Ownership always comes from get_current_user_id (the token),
never from the request.
"""

from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends

from app.auth import get_current_user_id
from app.db import get_connection
from app.platforms import SUPPORTED_PLATFORM
from app.repositories.targets import TargetRepository
from app.schemas.targets import TargetCreate, TargetResponse

router = APIRouter(prefix="/targets", tags=["targets"])


def get_target_repository(
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> TargetRepository:
    return TargetRepository(conn)


Repo = Annotated[TargetRepository, Depends(get_target_repository)]
OwnerId = Annotated[str, Depends(get_current_user_id)]


@router.post("", status_code=201, response_model=TargetResponse)
def create_target(payload: TargetCreate, owner_id: OwnerId, repo: Repo) -> dict:
    """S1-015: create a target owned by the signed-in user."""
    return repo.create(owner_id, payload.name, SUPPORTED_PLATFORM, payload.source_url)
