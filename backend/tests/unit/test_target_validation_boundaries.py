"""S1-016: the exact edges of target validation.

test_target_validation.py checks that obviously good and obviously bad input is
handled. These tests pin down the boundaries: one character under, at and over
each limit, and the point where a link stops being a Google Maps link. The limits
are written as plain numbers on purpose: they are part of the behavior we promise.
"""

import pytest
from pydantic import ValidationError

from app.platforms import validate_source_url
from app.schemas.targets import TargetCreate

PREFIX = "https://www.google.com/maps/place/"
VALID = PREFIX + "Joes+Pizza"


def _link_of_length(length: int) -> str:
    return PREFIX + "a" * (length - len(PREFIX))


# --- how long a link may be ---------------------------------------------------


def test_a_link_of_exactly_2048_characters_is_accepted():
    link = _link_of_length(2048)
    assert len(link) == 2048
    assert validate_source_url(link) == link


def test_a_link_of_2049_characters_is_refused_as_too_long():
    link = _link_of_length(2049)
    assert len(link) == 2049
    with pytest.raises(ValueError, match="too long"):
        validate_source_url(link)


# --- how long a name may be ---------------------------------------------------


@pytest.mark.parametrize("name", ["J", "Joe's Pizza", "x" * 199, "x" * 200, "Café Zoë"])
def test_names_up_to_200_characters_are_accepted(name):
    assert TargetCreate(name=name, source_url=VALID).name == name


def test_a_name_of_201_characters_is_refused():
    with pytest.raises(ValidationError, match="200"):
        TargetCreate(name="x" * 201, source_url=VALID)


def test_the_name_limit_counts_the_trimmed_name():
    target = TargetCreate(name="  " + "x" * 200 + "  ", source_url=VALID)
    assert target.name == "x" * 200


def test_spaces_inside_a_name_are_kept():
    assert TargetCreate(name="Joe's  Pizza", source_url=VALID).name == "Joe's  Pizza"


@pytest.mark.parametrize("name", ["", " ", "\t", "\n", " \t\n "])
def test_a_name_with_nothing_but_whitespace_is_refused(name):
    with pytest.raises(ValidationError, match="Enter a name"):
        TargetCreate(name=name, source_url=VALID)


# --- the port in the link -----------------------------------------------------


def test_the_standard_https_port_is_accepted():
    link = "https://www.google.com:443/maps/place/x"
    assert validate_source_url(link) == link


@pytest.mark.parametrize("port", ["444", "80", "8443"])
def test_any_other_port_is_refused(port):
    with pytest.raises(ValueError, match="doesn't look like a Google Maps link"):
        validate_source_url(f"https://www.google.com:{port}/maps/place/x")


@pytest.mark.parametrize("port", ["abc", "99999"])
def test_a_malformed_port_is_refused_as_not_a_valid_link(port):
    with pytest.raises(ValueError, match="valid link"):
        validate_source_url(f"https://www.google.com:{port}/maps/place/x")


# --- credentials hidden in the link -------------------------------------------


@pytest.mark.parametrize(
    "link",
    [
        "https://user:secret@www.google.com/maps/place/x",
        "https://user@www.google.com/maps/place/x",
        "https://:secret@www.google.com/maps/place/x",
    ],
)
def test_a_link_carrying_a_username_or_password_is_refused(link):
    with pytest.raises(ValueError, match="doesn't look like a Google Maps link"):
        validate_source_url(link)


# --- upper and lower case -------------------------------------------------------


@pytest.mark.parametrize(
    "link",
    [
        "https://WWW.Google.COM/maps/place/x",
        "HTTPS://www.google.com/maps/place/x",
        "https://Maps.Google.com/?cid=1",
    ],
)
def test_the_scheme_and_host_may_be_written_in_any_case(link):
    assert validate_source_url(link) == link


# --- where /maps starts on google.com -------------------------------------------


@pytest.mark.parametrize(
    "link",
    [
        "https://www.google.com/maps",
        "https://www.google.com/maps/",
        "https://www.google.com/maps/place/x",
        "https://www.google.com/maps?q=cafe",
    ],
)
def test_links_under_exactly_the_maps_path_are_accepted(link):
    assert validate_source_url(link) == link


@pytest.mark.parametrize(
    "link",
    [
        "https://www.google.com/mapsx/place/x",
        "https://www.google.com/map/place/x",
        "https://www.google.com/",
        "https://www.google.com/search/maps/x",
        "https://www.google.com/search?q=maps",
    ],
)
def test_other_google_pages_are_refused(link):
    with pytest.raises(ValueError, match="isn't from Google Maps"):
        validate_source_url(link)


# --- which Google domains count -------------------------------------------------


@pytest.mark.parametrize(
    "host",
    ["google.com", "www.google.com", "google.co.uk", "google.com.au", "www.google.de"],
)
def test_google_domains_for_other_countries_are_accepted(host):
    link = f"https://{host}/maps/place/x"
    assert validate_source_url(link) == link


@pytest.mark.parametrize(
    "host",
    [
        "google.xyz",
        "notgoogle.com",
        "google.com.evil.example",
        "www.google.com.evil.example",
        "google-maps.com",
    ],
)
def test_domains_that_only_resemble_google_are_refused(host):
    with pytest.raises(ValueError, match="isn't from Google Maps"):
        validate_source_url(f"https://{host}/maps/place/x")


# --- the other Google Maps link styles ------------------------------------------


@pytest.mark.parametrize(
    "link",
    [
        "https://maps.google.com/",
        "https://maps.google.com/?cid=12345",
        "https://maps.app.goo.gl/a",
        "https://maps.app.goo.gl/AbC123xyz",
        "https://goo.gl/maps/abc",
    ],
)
def test_map_and_share_link_styles_are_accepted(link):
    assert validate_source_url(link) == link


@pytest.mark.parametrize(
    "link",
    [
        "https://maps.app.goo.gl/",
        "https://maps.app.goo.gl",
        "https://goo.gl/maps",
        "https://goo.gl/mapsx/abc",
        "https://goo.gl/other/maps/abc",
        "https://goo.gl/abc",
    ],
)
def test_incomplete_share_links_are_refused(link):
    with pytest.raises(ValueError, match="isn't from Google Maps"):
        validate_source_url(link)


# --- the message tells the user what to fix --------------------------------------


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("", "Enter the link"),
        ("   ", "Enter the link"),
        ("http://www.google.com/maps/place/x", "https://"),
        ("ftp://www.google.com/maps/place/x", "https://"),
        ("www.google.com/maps/place/x", "https://"),
        ("https://www.google.com/maps/place/a b", "spaces"),
        ("https://www.google.com/maps/place/a\tb", "spaces"),
        ("https://www.google.com/maps/place/a\nb", "spaces"),
        ("https://www.yelp.com/biz/x", "isn't from Google Maps"),
    ],
)
def test_each_kind_of_bad_link_gets_its_own_message(link, expected):
    with pytest.raises(ValueError, match=expected):
        validate_source_url(link)


def test_a_link_with_spaces_only_at_the_ends_is_trimmed_not_refused():
    assert validate_source_url(f"  {VALID}\t\n") == VALID


def test_the_error_message_names_the_platform_the_user_should_use():
    with pytest.raises(ValueError) as refused:
        validate_source_url("https://www.yelp.com/biz/x")
    assert "Google Maps" in str(refused.value)


# --- hosts and schemes are matched exactly, not by alphabetical position ------------


@pytest.mark.parametrize(
    "host", ["a.example", "evil.example", "example.com", "goo.go", "zzz.example"]
)
def test_other_hosts_are_refused_on_a_goo_gl_style_path(host):
    # Guards against comparing hosts by sort order instead of by equality.
    with pytest.raises(ValueError, match="isn't from Google Maps"):
        validate_source_url(f"https://{host}/maps/abc")


@pytest.mark.parametrize("scheme", ["http", "ftp", "https2", "wss", "xyz"])
def test_only_the_https_scheme_is_accepted(scheme):
    # Includes schemes that sort before and after "https".
    with pytest.raises(ValueError, match="https://"):
        validate_source_url(f"{scheme}://www.google.com/maps/place/x")
