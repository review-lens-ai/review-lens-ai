import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app.auth import get_key_resolver
from app.core.config import get_settings
from app.main import app

SUPABASE_URL = "https://test-project.supabase.co"
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
def client(monkeypatch, trusted_key):
    monkeypatch.setenv("SUPABASE_URL", SUPABASE_URL)
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
