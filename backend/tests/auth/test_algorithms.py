"""Attacks on the signing algorithm."""

import jwt
from cryptography.hazmat.primitives import serialization

from tests.conftest import auth


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
