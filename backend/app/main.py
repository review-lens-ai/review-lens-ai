import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import me
from app.core.errors import register_error_handlers

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="ReviewLens API")
register_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

# Every router under /api must depend on get_current_user (S1-BR-004).
app.include_router(me.router, prefix="/api")


@app.get("/health")
def health():
    return {"status": "ok"}
