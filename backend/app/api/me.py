from typing import Annotated

from fastapi import APIRouter, Depends

from app.auth import CurrentUser, get_current_user

router = APIRouter(tags=["auth"])


@router.get("/me")
def read_me(user: Annotated[CurrentUser, Depends(get_current_user)]) -> dict:
    return {"user_id": user.id, "email": user.email}
