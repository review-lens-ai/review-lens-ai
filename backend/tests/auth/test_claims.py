"""Claims a token must carry, and the identity built from them."""

from dataclasses import FrozenInstanceError

import pytest

from app.auth import CurrentUser
from tests.conftest import auth


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


def test_the_verified_identity_cannot_be_changed_after_verification():
    user = CurrentUser(id="user-a")
    with pytest.raises(FrozenInstanceError):
        user.id = "user-b"  # type: ignore[misc]
