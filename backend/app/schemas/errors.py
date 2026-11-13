from pydantic import BaseModel


class FieldIssue(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[FieldIssue] | None = None


class ErrorResponse(BaseModel):
    """The body of every /api/v1 error response."""

    error: ErrorBody
