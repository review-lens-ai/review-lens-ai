import os
from dataclasses import dataclass
from functools import lru_cache


class ConfigError(RuntimeError):
    """Raised at startup/first use when required configuration is missing."""


@dataclass(frozen=True)
class Settings:
    supabase_url: str

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"


@lru_cache
def get_settings() -> Settings:
    url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    if not url:
        raise ConfigError(
            "SUPABASE_URL is not set. Copy backend/.env.example and fill it in."
        )
    return Settings(supabase_url=url)
