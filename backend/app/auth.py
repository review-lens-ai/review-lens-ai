"""Verifies the Supabase access token and exposes the trusted user identity.

Authentication only answers "who is calling". Ownership checks live in the
services/repositories that query user-scoped data (S1-BR-009).
"""

import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.core.config import get_settings
from app.core.errors import AuthenticationError

logger = logging.getLogger(__name__)

# Asymmetric only: never accept "none" or HS256 (algorithm-confusion attacks).
ALLOWED_ALGORITHMS = ["RS256", "ES256"]
AUDIENCE = "authenticated"

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: str
    email: str | None = None


class SigningKeyResolver:
    """Looks up the public key for a token. Replaced in tests."""

    def __init__(self, jwks_url: str):
        self._client = PyJWKClient(jwks_url, cache_keys=True, timeout=5)

    def key_for(self, token: str) -> Any:
        return self._client.get_signing_key_from_jwt(token).key


@lru_cache
def get_key_resolver() -> SigningKeyResolver:
    return SigningKeyResolver(get_settings().jwks_url)


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    resolver: Annotated[SigningKeyResolver, Depends(get_key_resolver)],
) -> CurrentUser:
    if creds is None or creds.scheme.lower() != "bearer" or not creds.credentials:
        raise AuthenticationError()
    token = creds.credentials
    try:
        key = resolver.key_for(token)
        claims = jwt.decode(
            token,
            key,
            algorithms=ALLOWED_ALGORITHMS,
            audience=AUDIENCE,
            issuer=get_settings().jwt_issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        # Detail goes to logs only; the client just learns the session is invalid.
        logger.warning("token rejected: %s", type(exc).__name__)
        raise AuthenticationError(
            "Your session is invalid or has expired. Please sign in again."
        ) from exc
    return CurrentUser(id=claims["sub"], email=claims.get("email"))
