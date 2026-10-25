import os
import time

# Fake settings, set before importing the app: main.py checks them at import (fail fast).
# Real env vars override backend/.env, so tests never use anyone's real values.
SUPABASE_URL = "https://test-project.supabase.co"
os.environ["SUPABASE_URL"] = SUPABASE_URL
os.environ["DATABASE_URL"] = "postgresql://user:fake-password@localhost:5432/test"

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.auth import get_key_resolver
from app.core.config import get_settings
from app.main import app

ISSUER = f"{SUPABASE_URL}/auth/v1"


class FakeResolver:
    def __init__(self, public_key):
        self.public_key = public_key

    def key_for(self, token: str):
        return self.public_key


@pytest.fixture(scope="session")
def trusted_key():
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture
def client(trusted_key):
    get_settings.cache_clear()
    app.dependency_overrides[get_key_resolver] = lambda: FakeResolver(
        trusted_key.public_key()
    )
    yield TestClient(app)
    app.dependency_overrides.clear()
    get_settings.cache_clear()


@pytest.fixture
def make_token(trusted_key):
    def _make(user_id="user-a", key=None, alg="ES256", **overrides):
        claims = {
            "sub": user_id,
            "email": f"{user_id}@example.com",
            "aud": "authenticated",
            "iss": ISSUER,
            "exp": int(time.time()) + 3600,
        }
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or trusted_key, algorithm=alg)

    return _make


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}
