"""S1-016: target input is validated at the backend, accepting good input and rejecting bad."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import register_error_handlers
from app.platforms import validate_source_url
from app.schemas.targets import MAX_NAME_LENGTH, TargetCreate

VALID_URLS = [
    "https://www.google.com/maps/place/Joes+Pizza/@40.73,-73.99,17z/data=!1s0x1:0x2",
    "https://google.com/maps/place/Joes+Pizza/data=!4m5!1s0x89c2537da9b3163d:0x2be30fcff506fec8",
    "https://www.google.co.uk/maps/place/Pret/data=!1s0x1:0x2",
    "https://maps.google.com/maps?ftid=0x1:0x2",
    "https://www.google.com/maps?ftid=0x89c2537da9b3163d:0x2be30fcff506fec8",
    "https://maps.app.goo.gl/AbC123xyz",
    "https://goo.gl/maps/AbC123xyz",
]

INVALID_URLS = [
    "",
    "   ",
    "not a url",
    "joes pizza",
    "http://www.google.com/maps/place/x",  # not https
    "ftp://www.google.com/maps/place/x",
    "javascript:alert(1)",
    "https://www.yelp.com/biz/joes-pizza",  # another platform
    "https://www.google.com/search?q=joes+pizza",  # Google, but not Maps
    "https://evil.example/www.google.com/maps/place/x",
    "https://www.google.com.evil.example/maps/place/x",
    "https://user:pass@www.google.com/maps/place/x",
    "https://www.google.com:8443/maps/place/x",
    "https://maps.app.goo.gl/",  # share link with no id
    "https://www.google.com/maps/place/a b",
    "https://www.google.com/maps/" + "a" * 2100,
    "https://www.google.com/maps",  # Maps, but no place
    "https://www.google.com/maps/",
    "https://www.google.com/maps/place/",  # place path with no place
    "https://www.google.com/maps/search/pizza+newark",  # search results
    "https://www.google.com/maps/dir/Newark/Boston",  # directions
    "https://www.google.com/maps/@40.73,-73.99,15z",  # a map view
    "https://maps.google.com/",
    "https://maps.google.com/?q=pizza",  # search, not a place id
    "https://maps.google.com/?cid=12345",  # cid has no place id we can use
    "https://www.google.com/maps/place/Joes+Pizza",  # no place id in the link
    "https://www.google.com/maps?ftid=abc",  # ftid must be a place id
    "https://goo.gl/maps/",
    "https://www.google.com/maps/place/x\x00y",  # hidden character
]


@pytest.mark.parametrize("url", VALID_URLS)
def test_google_maps_links_are_accepted(url):
    assert validate_source_url(url) == url


@pytest.mark.parametrize("url", INVALID_URLS)
def test_links_that_are_not_google_maps_are_rejected(url):
    with pytest.raises(ValueError):
        validate_source_url(url)


def test_surrounding_whitespace_is_trimmed_from_the_link():
    assert (
        validate_source_url("  https://maps.app.goo.gl/AbC123  ")
        == "https://maps.app.goo.gl/AbC123"
    )


def test_valid_target_input_is_accepted_and_cleaned():
    target = TargetCreate(name="  Joe's Pizza  ", source_url=VALID_URLS[0])
    assert target.name == "Joe's Pizza"


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_target_name_is_rejected(name):
    with pytest.raises(ValidationError):
        TargetCreate(name=name, source_url=VALID_URLS[0])


def test_overlong_target_name_is_rejected():
    with pytest.raises(ValidationError):
        TargetCreate(name="x" * (MAX_NAME_LENGTH + 1), source_url=VALID_URLS[0])


def test_missing_fields_are_rejected():
    with pytest.raises(ValidationError):
        TargetCreate()


def test_an_owner_id_sent_by_the_client_is_not_kept():
    target = TargetCreate(
        name="Joe's", source_url=VALID_URLS[0], owner_id="someone-else"
    )
    assert "owner_id" not in target.model_dump()


@pytest.fixture
def validation_client():
    app = FastAPI()
    register_error_handlers(app)

    @app.post("/echo")
    def echo(target: TargetCreate):
        return target

    return TestClient(app)


def test_invalid_input_returns_a_422_with_one_readable_message_per_field(
    validation_client,
):
    r = validation_client.post(
        "/echo", json={"name": " ", "source_url": "https://yelp.com/biz/x"}
    )
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    issues = {d["field"]: d["message"] for d in error["details"]}
    assert set(issues) == {"name", "source_url"}
    assert "Google Maps" in issues["source_url"]


def test_missing_fields_are_reported_as_required(validation_client):
    r = validation_client.post("/echo", json={})
    fields = {d["field"]: d["message"] for d in r.json()["error"]["details"]}
    assert fields == {
        "name": "This field is required.",
        "source_url": "This field is required.",
    }


def test_validation_errors_do_not_echo_submitted_values(validation_client):
    secret = "https://user:hunter2@www.google.com/maps/place/x"
    r = validation_client.post("/echo", json={"name": "ok", "source_url": secret})
    assert r.status_code == 422
    assert "hunter2" not in r.text


def test_valid_input_passes_through_the_endpoint(validation_client):
    r = validation_client.post(
        "/echo", json={"name": "Joe's", "source_url": VALID_URLS[3]}
    )
    assert r.status_code == 200


@pytest.mark.parametrize("name", ["!!!", "---", "   ...   ", "🍕🍕"])
def test_a_name_with_no_letter_or_number_is_rejected(name):
    with pytest.raises(ValidationError):
        TargetCreate(name=name, source_url=VALID_URLS[0])


@pytest.mark.parametrize("name", ["Joe\nPizza", "Joe\tPizza", "Joe\x00Pizza"])
def test_a_name_with_line_breaks_or_hidden_characters_is_rejected(name):
    with pytest.raises(ValidationError):
        TargetCreate(name=name, source_url=VALID_URLS[0])


@pytest.mark.parametrize("name", ["7-Eleven", "Café Zoë", "A&W", "42"])
def test_ordinary_names_with_symbols_or_digits_are_accepted(name):
    assert TargetCreate(name=name, source_url=VALID_URLS[0]).name == name


def test_a_link_without_a_place_gets_a_message_that_says_so():
    with pytest.raises(ValueError, match="one specific place"):
        validate_source_url("https://www.google.com/maps/search/pizza")


def test_a_non_maps_link_still_gets_the_wrong_platform_message():
    with pytest.raises(ValueError, match="isn't from Google Maps"):
        validate_source_url("https://www.yelp.com/biz/joes-pizza")
