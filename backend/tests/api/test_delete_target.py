"""S1-012: only the owner can delete a target; anyone else gets a 404 and the target survives."""

import uuid

from tests.conftest import auth

URL = "https://www.google.com/maps/place/Joes+Pizza/@40.73,-73.99,17z/data=!3m1!4b1!4m6!3m5!1s0x89c2537da9b3163d:0x2be30fcff506fec8!8m2!3d40.73!4d-73.99"


def test_owner_deletes_their_target_and_it_is_gone(client, repo, make_token):
    target = repo.create("user-a", "Joe's", "google_maps", URL)
    url = f"/api/v1/targets/{target['id']}"

    assert client.delete(url, headers=auth(make_token("user-a"))).status_code == 204
    assert client.get(url, headers=auth(make_token("user-a"))).status_code == 404


def test_user_b_delete_of_user_a_target_is_not_found_and_target_survives(
    client, repo, make_token
):
    target = repo.create("user-a", "Joe's", "google_maps", URL)

    r = client.delete(
        f"/api/v1/targets/{target['id']}", headers=auth(make_token("user-b"))
    )

    assert r.status_code == 404
    assert str(target["id"]) in repo.rows


def test_deleting_an_unknown_target_is_not_found(client, repo, make_token):
    r = client.delete(
        f"/api/v1/targets/{uuid.uuid4()}", headers=auth(make_token("user-a"))
    )
    assert r.status_code == 404


def test_deleting_without_a_token_is_rejected(client, repo, make_token):
    target = repo.create("user-a", "Joe's", "google_maps", URL)

    r = client.delete(f"/api/v1/targets/{target['id']}")

    assert r.status_code == 401
    assert str(target["id"]) in repo.rows
