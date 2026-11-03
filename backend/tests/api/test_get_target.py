"""S1-018: opening a target returns its workspace data, for its owner only."""

from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza"
UNKNOWN_ID = "00000000-0000-0000-0000-000000000000"


def _create(client, token, name="Joe's Pizza"):
    r = client.post(
        "/api/v1/targets", json={"name": name, "source_url": URL}, headers=auth(token)
    )
    return r.json()["id"]


def test_owner_opens_their_target_with_its_workspace_details(client, repo, make_token):
    token = make_token("user-a")
    target_id = _create(client, token)

    r = client.get(f"/api/v1/targets/{target_id}", headers=auth(token))

    assert r.status_code == 200
    body = r.json()
    assert body["id"] == target_id
    assert body["name"] == "Joe's Pizza"
    assert body["platform"] == "google_maps"
    assert body["source_url"] == URL
    assert body["ingestion_status"] is None


def test_opening_an_unknown_target_returns_not_found(client, repo, make_token):
    r = client.get(f"/api/v1/targets/{UNKNOWN_ID}", headers=auth(make_token()))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_a_malformed_target_id_is_rejected_cleanly(client, repo, make_token):
    r = client.get("/api/v1/targets/not-a-uuid", headers=auth(make_token()))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_user_b_opening_user_a_target_gets_not_found_and_no_data(
    client, repo, make_token
):
    target_id = _create(client, make_token("user-a"), name="Secret Cafe")

    r = client.get(f"/api/v1/targets/{target_id}", headers=auth(make_token("user-b")))

    assert r.status_code == 404
    assert "Secret Cafe" not in r.text
    # Indistinguishable from a target that does not exist.
    unknown = client.get(
        f"/api/v1/targets/{UNKNOWN_ID}", headers=auth(make_token("user-b"))
    )
    assert r.json() == unknown.json()


def test_opening_a_target_without_a_token_is_rejected(client, repo, make_token):
    target_id = _create(client, make_token("user-a"))
    r = client.get(f"/api/v1/targets/{target_id}")
    assert r.status_code == 401
