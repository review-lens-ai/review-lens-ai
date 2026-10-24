from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Backend settings, loaded from environment variables and backend/.env."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # Required at startup
    # JWT verification uses Supabase JWKS via SUPABASE_URL — confirm in S1-009
    supabase_url: str
    database_url: SecretStr

    # Optional secrets, checked when the feature that needs them runs
    review_provider_api_key: SecretStr | None = None
    openrouter_api_key: SecretStr | None = None 

    # Non-secret settings with defaults
    cors_origins: str = "http://localhost:3000"  # comma-separated
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"
    max_reviews_per_run: int = 1000

    def __init__(self, **kwargs):
        try:
            super().__init__(**kwargs)
        
        except ValidationError as error:
            #builds a list of missing variable name
            missing = [
                str(err["loc"][0]).upper()
                for err in error.errors()
                if err["type"] == "missing"
            ]
            if not missing:
                raise
            # "from None" drops the original error, which can echo input values (secrets)
            raise RuntimeError(
                f"Missing required setting: {', '.join(missing)}"
            ) from None


@lru_cache
def get_settings() -> Settings:
    return Settings()
