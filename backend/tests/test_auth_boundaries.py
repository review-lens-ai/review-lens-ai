"""Boundary, edge and security cases for token verification (app/auth.py).

test_auth.py covers the main paths through HTTP. These tests add:
- the exact edges (a token that expires one second ago, a scheme in any letter case),
- attacks on the algorithm and claims,
- direct calls to get_current_user, because FastAPI's HTTPBearer filters some bad
  headers before our code runs, which would otherwise leave our own checks untested.
"""

import time
from dataclasses import FrozenInstanceError

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from fastapi.security import HTTPAuthorizationCredentials

from app import auth as auth_module
from app.auth import CurrentUser, SigningKeyResolver, get_current_user, get_key_resolver
from app.core.config import get_settings
from app.core.errors import AuthenticationError
from tests.conftest import SUPABASE_URL, FakeResolver, auth

SIGN_IN_MESSAGE = "Please sign in to continue."
SESSION_MESSAGE = "Your session is invalid or has expired. Please sign in again."


@pytest.fixture
def resolver(trusted_key):
    return FakeResolver(trusted_key.public_key())


def _creds(scheme: str, token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme=scheme, credentials=token)


# --- the scheme in the Authorization header -----------------------------------


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_bearer_scheme_is_accepted_in_any_letter_case(scheme, resolver, make_token):
    user = get_current_user(_creds(scheme, make_token("user-a")), resolver)
    assert user == CurrentUser(id="user-a", email="user-a@example.com")


@pytest.mark.parametrize("scheme", ["Basic", "Token", "Aaa", "Bearer2", ""])
def test_other_schemes_are_refused_even_with_a_valid_token(
    scheme, resolver, make_token
):
    with pytest.raises(AuthenticationError) as refused:
        get_current_user(_creds(scheme, make_token("user-a")), resolver)
    assert refused.value.message == SIGN_IN_MESSAGE


def test_no_credentials_are_refused_with_the_sign_in_message(resolver):
    with pytest.raises(AuthenticationError) as refused:
        get_current_user(None, resolver)
    assert refused.value.message == SIGN_IN_MESSAGE


def test_an_empty_token_is_refused_with_the_sign_in_message(resolver):
    with pytest.raises(AuthenticationError) as refused:
        get_current_user(_creds("Bearer", ""), resolver)
    assert refused.value.message == SIGN_IN_MESSAGE


def test_an_invalid_token_is_refused_with_the_session_message(resolver):
    with pytest.raises(AuthenticationError) as refused:
        get_current_user(_creds("Bearer", "not-a-jwt"), resolver)
    assert refused.value.message == SESSION_MESSAGE


# --- time boundaries ----------------------------------------------------------


def test_a_token_that_expires_in_the_future_is_accepted(client, make_token):
    token = make_token("user-a", exp=int(time.time()) + 30)
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 200


def test_a_token_that_expired_one_second_ago_is_refused(client, make_token):
    token = make_token("user-a", exp=int(time.time()) - 1)
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 401


def test_a_token_that_is_not_valid_yet_is_refused(client, make_token):
    token = make_token("user-a", nbf=int(time.time()) + 3600)
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 401


# --- claims the token must carry ----------------------------------------------


def test_a_token_without_an_expiry_is_refused(client, make_token):
    assert (
        client.get("/api/v1/me", headers=auth(make_token(exp=None))).status_code == 401
    )


def test_a_token_without_a_subject_is_refused(client, make_token):
    assert (
        client.get("/api/v1/me", headers=auth(make_token(sub=None))).status_code == 401
    )


def test_a_token_for_another_audience_is_refused(client, make_token):
    assert (
        client.get("/api/v1/me", headers=auth(make_token(aud="anon"))).status_code
        == 401
    )


def test_a_token_from_another_issuer_is_refused(client, make_token):
    token = make_token(iss="https://evil.example/auth/v1")
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 401


def test_a_token_without_an_email_still_identifies_the_user(client, make_token):
    r = client.get("/api/v1/me", headers=auth(make_token("user-a", email=None)))
    assert r.status_code == 200
    assert r.json() == {"user_id": "user-a", "email": None}


# --- attacks on the signing algorithm -----------------------------------------


def test_an_unsigned_token_is_refused(client):
    unsigned = jwt.encode(
        {"sub": "user-a", "aud": "authenticated"}, key=None, algorithm="none"
    )
    assert client.get("/api/v1/me", headers=auth(unsigned)).status_code == 401


def test_algorithm_confusion_with_the_public_key_as_secret_is_refused(
    client, trusted_key
):
    # PyJWT refuses to build this token, so assemble it by hand like an attacker would.
    import base64
    import hashlib
    import hmac
    import json

    public_pem = trusted_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    header = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = b64(json.dumps({"sub": "user-a", "aud": "authenticated"}).encode())
    signing_input = header + b"." + payload
    signature = b64(hmac.new(public_pem, signing_input, hashlib.sha256).digest())
    forged = (signing_input + b"." + signature).decode()

    assert client.get("/api/v1/me", headers=auth(forged)).status_code == 401


# --- the identity object ------------------------------------------------------


def test_the_verified_identity_cannot_be_changed_after_verification():
    user = CurrentUser(id="user-a")
    with pytest.raises(FrozenInstanceError):
        user.id = "user-b"  # type: ignore[misc]


# --- fetching Supabase's public keys ------------------------------------------


class _RecordingJWKClient:
    """Stands in for PyJWKClient: records how it was built, and serves a fixed key."""

    instances: list["_RecordingJWKClient"] = []

    def __init__(self, url, **options):
        self.url = url
        self.options = options
        self.asked_for: list[str] = []
        _RecordingJWKClient.instances.append(self)

    def get_signing_key_from_jwt(self, token):
        self.asked_for.append(token)

        class _Key:
            key = "the-public-key"

        return _Key()


@pytest.fixture
def recording_jwk_client(monkeypatch):
    _RecordingJWKClient.instances = []
    monkeypatch.setattr(auth_module, "PyJWKClient", _RecordingJWKClient)
    get_settings.cache_clear()
    get_key_resolver.cache_clear()
    yield _RecordingJWKClient
    get_key_resolver.cache_clear()
    get_settings.cache_clear()


def test_the_key_fetcher_caches_keys_and_gives_up_after_five_seconds(
    recording_jwk_client,
):
    # Caching: avoids a network call to Supabase on every request.
    # Timeout: a slow Supabase must not hang our API.
    SigningKeyResolver("https://example.test/jwks.json")

    (client,) = recording_jwk_client.instances
    assert client.options["cache_keys"] is True
    assert client.options["timeout"] == 5


def test_the_resolver_returns_the_key_the_token_names(recording_jwk_client):
    resolver = SigningKeyResolver("https://example.test/jwks.json")

    assert resolver.key_for("some.jwt.token") == "the-public-key"
    assert recording_jwk_client.instances[0].asked_for == ["some.jwt.token"]


def test_the_default_resolver_uses_the_project_key_set_and_is_built_once(
    recording_jwk_client,
):
    first, second = get_key_resolver(), get_key_resolver()

    assert first is second
    assert len(recording_jwk_client.instances) == 1
    assert recording_jwk_client.instances[0].url == (
        f"{SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    )
