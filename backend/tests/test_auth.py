from cryptography.hazmat.primitives.asymmetric import ec

from tests.conftest import auth


def test_valid_token_returns_verified_user_id(client, make_token):
    r = client.get("/api/me", headers=auth(make_token("user-a")))
    assert r.status_code == 200
    assert r.json()["user_id"] == "user-a"


def test_missing_token_is_rejected_without_data(client):
    r = client.get("/api/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert "user_id" not in r.text


def test_forged_token_signed_by_attacker_key_is_rejected(client, make_token):
    attacker = ec.generate_private_key(ec.SECP256R1())
    r = client.get("/api/me", headers=auth(make_token("user-b", key=attacker)))
    assert r.status_code == 401


def test_expired_token_is_rejected(client, make_token):
    r = client.get("/api/me", headers=auth(make_token(exp=1)))
    assert r.status_code == 401
