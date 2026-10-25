from fastapi import FastAPI, Request
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


def error_body(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message}}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, exc.message),
            headers=headers,
        )
