import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import register_error_handlers

settings = get_settings()  # fail fast if required settings are missing

logging.basicConfig(level=settings.log_level)

# Operation ids are the route function names (e.g. get_latest_ingestion_run),
# which is what Orval names the generated frontend client after.
app = FastAPI(
    title="ReviewLens API",
    generate_unique_id_function=lambda route: route.name,
)
register_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(api_router)


@app.get("/health")
def health():
    return {"status": "ok"}
