from fastapi import FastAPI

from app.config import get_settings

get_settings()  # fail fast if required settings are missing

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}