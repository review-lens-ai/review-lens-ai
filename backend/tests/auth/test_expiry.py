"""Exact time edges of token validity (exp and nbf)."""

import time

from tests.conftest import auth


def test_a_token_that_expires_in_the_future_is_accepted(client, make_token):
    token = make_token("user-a", exp=int(time.time()) + 30)
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 200


def test_a_token_that_expired_one_second_ago_is_refused(client, make_token):
    token = make_token("user-a", exp=int(time.time()) - 1)
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 401


def test_a_token_that_is_not_valid_yet_is_refused(client, make_token):
    token = make_token("user-a", nbf=int(time.time()) + 3600)
    assert client.get("/api/v1/me", headers=auth(token)).status_code == 401
