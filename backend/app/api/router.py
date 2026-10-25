from fastapi import APIRouter, Depends

from app.api import me, targets
from app.auth import get_current_user

# Every endpoint under /api/v1 requires a verified user (S1-BR-004).
# Public endpoints (e.g. /health) live outside this router.
api_router = APIRouter(prefix="/api/v1", dependencies=[Depends(get_current_user)])
api_router.include_router(me.router)
api_router.include_router(targets.router)
