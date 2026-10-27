from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code = 500
    code = "INTERNAL_ERROR"
    message = "Something went wrong. Please try again."

    def __init__(self, message: str | None = None):
        super().__init__(message or self.message)
        self.message = message or self.message


class AuthenticationError(AppError):
    status_code = 401
    code = "AUTHENTICATION_REQUIRED"
    message = "Please sign in to continue."


class NotFoundError(AppError):
    """Also used when a resource exists but belongs to another user, so a
    caller cannot tell the two cases apart (S1-BR-008)."""

    status_code = 404
    code = "NOT_FOUND"
    message = "We couldn't find what you were looking for."


def error_body(code: str, message: str, details: list[dict] | None = None) -> dict:
    body: dict = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


def _field_issue(err: dict) -> dict:
    """One validation problem as {field, message}, phrased for end users."""
    # loc looks like ("body", "source_url"); the last item is the field name.
    field = str(err["loc"][-1]) if err["loc"] else "body"
    if err["type"] == "missing":
        message = "This field is required."
    elif err["type"] == "json_invalid":
        field, message = "body", "The request could not be read."
    elif err["type"].startswith("string_"):
        message = "Enter text for this field."
    else:
        # Our own validators raise ValueError("<friendly text>"); pydantic prefixes it.
        message = err["msg"].removeprefix("Value error, ")
    return {"field": field, "message": message}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Built from our own messages only: never echo submitted values back.
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR",
                "Please fix the highlighted fields and try again.",
                [_field_issue(err) for err in exc.errors()],
            ),
        )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message),
            headers=headers,
        )
