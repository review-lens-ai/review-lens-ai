from pydantic import BaseModel


class CurrentUserResponse(BaseModel):
    user_id: str
    email: str | None
