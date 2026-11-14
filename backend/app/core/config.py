from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"  # backend/.env


class Settings(BaseSettings):
    """Backend settings, loaded from environment variables and backend/.env."""

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # Required at startup
    # Also used to verify Supabase JWTs (see jwks_url, jwt_issuer)
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

    # Q&A (S2-010). qa_model is an OpenRouter model id, e.g. "provider/model-name".
    qa_model: str | None = None
    qa_max_tool_calls: int = 6
    qa_timeout_seconds: float = 30
    # "tools": the model calls the review tools. "full": every review is sent in
    # one message (fallback), used only while the target's review text fits in
    # qa_full_mode_max_characters (about 30,000 tokens); larger targets use tools.
    qa_mode: Literal["tools", "full"] = "tools"
    qa_full_mode_max_characters: int = 120_000

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    def __init__(self, **kwargs):
        try:
            super().__init__(**kwargs)

        except ValidationError as error:
            # builds a list of missing variable name
            missing = [
                str(err["loc"][0]).upper()
                for err in error.errors()
                if err["type"] == "missing"
            ]
            if not missing:
                raise
            # "from None" drops the original error, which can echo secret values
            raise RuntimeError(
                f"Missing required setting: {', '.join(missing)}"
            ) from None


@lru_cache
def get_settings() -> Settings:
    return Settings()
