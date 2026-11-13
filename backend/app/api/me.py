from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth import CurrentUser, get_current_user
from app.schemas.users import CurrentUserResponse

router = APIRouter(tags=["auth"])


@router.get("/me", response_model=CurrentUserResponse)
def read_me(user: Annotated[CurrentUser, Depends(get_current_user)]) -> dict:
    return {"user_id": user.id, "email": user.email}
