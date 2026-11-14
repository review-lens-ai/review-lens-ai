"""Fetching and caching Supabase's public signing keys."""

import ssl

import certifi
import pytest

from app import auth as auth_module
from app.auth import SigningKeyResolver, get_key_resolver
from app.core.config import get_settings
from tests.conftest import SUPABASE_URL


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


def test_the_key_fetcher_trusts_certifi_not_the_interpreter(recording_jwk_client):
    # A python.org Python ships without CA certificates, so relying on the
    # interpreter made every key fetch fail and every sign-in bounce with a 401.
    SigningKeyResolver("https://example.test/jwks.json")

    (client,) = recording_jwk_client.instances
    context = client.options["ssl_context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    # Exactly certifi's certificates. A context built from the interpreter's own
    # certificates has a different count (none at all on a bare python.org Python),
    # so switching back to the default fails here on every machine.
    certifi_only = ssl.create_default_context(cafile=certifi.where())
    expected = certifi_only.cert_store_stats()["x509_ca"]
    assert expected > 0
    assert context.cert_store_stats()["x509_ca"] == expected


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
