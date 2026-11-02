"""The Authorization header scheme and the messages for refused credentials.

These call get_current_user directly, because FastAPI's HTTPBearer filters some
bad headers before our code runs, which would otherwise leave our own checks
untested.
"""

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import CurrentUser, get_current_user
from app.core.errors import AuthenticationError
from tests.conftest import FakeResolver

SIGN_IN_MESSAGE = "Please sign in to continue."
SESSION_MESSAGE = "Your session is invalid or has expired. Please sign in again."


@pytest.fixture
def resolver(trusted_key):
    return FakeResolver(trusted_key.public_key())


def _creds(scheme: str, token: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme=scheme, credentials=token)


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
