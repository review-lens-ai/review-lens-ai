"""S1-017: a user sees their own targets, and only theirs."""

from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"


def _create(client, token, name):
    return client.post(
        "/api/v1/targets", json={"name": name, "source_url": URL}, headers=auth(token)
    )


def _names(client, token):
    r = client.get("/api/v1/targets", headers=auth(token))
    assert r.status_code == 200
    return {t["name"] for t in r.json()["items"]}


def test_user_a_sees_user_a_targets(client, repo, make_token):
    token_a = make_token("user-a")
    _create(client, token_a, "A one")
    _create(client, token_a, "A two")
    assert _names(client, token_a) == {"A one", "A two"}


def test_user_b_sees_user_b_targets_and_not_user_a_targets(client, repo, make_token):
    _create(client, make_token("user-a"), "A one")
    token_b = make_token("user-b")
    _create(client, token_b, "B one")

    assert _names(client, token_b) == {"B one"}


def test_each_listed_target_shows_what_is_needed_to_identify_it(
    client, repo, make_token
):
    token = make_token("user-a")
    _create(client, token, "Joe's Pizza")
    item = client.get("/api/v1/targets", headers=auth(token)).json()["items"][0]

    assert item["name"] == "Joe's Pizza"
    assert item["platform"] == "google_maps"
    assert item["source_url"] == URL


def test_user_with_no_targets_gets_an_empty_list(client, repo, make_token):
    r = client.get("/api/v1/targets", headers=auth(make_token("user-new")))
    assert r.status_code == 200
    assert r.json() == {"items": []}


def test_listing_without_a_token_is_rejected_and_returns_no_data(
    client, repo, make_token
):
    _create(client, make_token("user-a"), "A one")
    r = client.get("/api/v1/targets")
    assert r.status_code == 401
    assert "A one" not in r.text
