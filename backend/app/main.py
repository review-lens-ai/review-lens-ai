import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import me
from app.core.config import get_settings
from app.core.errors import register_error_handlers

settings = get_settings()  # fail fast if required settings are missing

logging.basicConfig(level=settings.log_level)

app = FastAPI(title="ReviewLens API")
register_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

# Every router under /api must depend on get_current_user (S1-BR-004).
app.include_router(me.router, prefix="/api/v1")


@app.get("/health")
def health():
    return {"status": "ok"}
