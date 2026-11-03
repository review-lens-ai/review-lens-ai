"""Ingestion result endpoints (S1-023), mounted under /api/v1/targets.

The router is mounted on api_router, so every endpoint here already requires a
verified user. Ownership comes from the token, and another user's target is a
404, indistinguishable from one that does not exist (S1-BR-008).
"""

from typing import Annotated
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends

from app.auth import get_current_user_id
from app.db import get_connection
from app.repositories.ingestion_runs import IngestionRunRepository
from app.schemas.ingestion_runs import LatestIngestionRun

router = APIRouter(prefix="/targets", tags=["ingestion"])


def get_ingestion_run_repository(
    conn: Annotated[psycopg.Connection, Depends(get_connection)],
) -> IngestionRunRepository:
    return IngestionRunRepository(conn)


Runs = Annotated[IngestionRunRepository, Depends(get_ingestion_run_repository)]
OwnerId = Annotated[str, Depends(get_current_user_id)]


@router.get("/{target_id}/ingestion-runs/latest", response_model=LatestIngestionRun)
def get_latest_ingestion_run(target_id: UUID, owner_id: OwnerId, runs: Runs) -> dict:
    """S1-023: the newest ingestion attempt for one of the user's targets, so the
    workspace can show running / succeeded / failed. `run` is null when the
    target was never ingested."""
    return {"run": runs.latest_for_target(target_id, owner_id)}
