"""S1-015: a signed-in user can create a target, owned by them and nobody else."""

from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"


def _post(client, token, body):
    return client.post("/api/v1/targets", json=body, headers=auth(token))


def test_signed_in_user_creates_a_target(client, repo, make_token):
    r = _post(client, make_token("user-a"), {"name": "Joe's Pizza", "source_url": URL})

    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "Joe's Pizza"
    assert body["platform"] == "google_maps"
    assert body["source_url"] == URL
    assert body["ingestion_status"] is None
    assert body["id"] and body["created_at"]


def test_target_is_owned_by_the_user_in_the_token(client, repo, make_token):
    r = _post(client, make_token("user-a"), {"name": "Joe's", "source_url": URL})
    assert repo.owners[r.json()["id"]] == "user-a"


def test_an_owner_id_in_the_body_is_ignored(client, repo, make_token):
    r = _post(
        client,
        make_token("user-a"),
        {"name": "Joe's", "source_url": URL, "owner_id": "user-b", "user_id": "user-b"},
    )
    assert r.status_code == 201
    assert repo.owners[r.json()["id"]] == "user-a"
    assert "owner_id" not in r.json()


def test_a_client_cannot_choose_the_platform(client, repo, make_token):
    r = _post(
        client, make_token(), {"name": "Joe's", "source_url": URL, "platform": "yelp"}
    )
    assert r.json()["platform"] == "google_maps"


def test_request_without_a_token_is_rejected_and_nothing_is_created(client, repo):
    r = client.post("/api/v1/targets", json={"name": "Joe's", "source_url": URL})
    assert r.status_code == 401
    assert repo.create_calls == 0


def test_missing_name_is_rejected_and_nothing_is_created(client, repo, make_token):
    r = _post(client, make_token(), {"source_url": URL})
    assert r.status_code == 422
    assert r.json()["error"]["details"][0] == {
        "field": "name",
        "message": "This field is required.",
    }
    assert repo.create_calls == 0


def test_missing_link_is_rejected_and_nothing_is_created(client, repo, make_token):
    r = _post(client, make_token(), {"name": "Joe's"})
    assert r.status_code == 422
    assert repo.create_calls == 0


def test_a_link_from_another_platform_is_rejected_and_nothing_is_created(
    client, repo, make_token
):
    r = _post(
        client,
        make_token(),
        {"name": "Joe's", "source_url": "https://www.yelp.com/biz/joes-pizza"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"
    assert repo.create_calls == 0
