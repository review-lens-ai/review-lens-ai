"""Turning a Google Maps link into the place id SerpApi expects."""

import re
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

from app.adapters.serpapi.errors import (
    ReviewFetchTimeoutError,
    UnsupportedSourceUrlError,
)

MAX_REDIRECTS = 5

# A Google Maps place id as SerpApi expects it,
# e.g. 0x89c2537da9b3163d:0x2be30fcff506fec8
_DATA_ID = re.compile(r"0x[0-9a-fA-F]+:0x[0-9a-fA-F]+")
# Full place links carry it after "!1s" in their data= part.
_DATA_ID_IN_LINK = re.compile(r"!1s(0x[0-9a-fA-F]+:0x[0-9a-fA-F]+)")
_GOOGLE_HOST = re.compile(
    r"^(www\.|maps\.)?google\.(com|co\.[a-z]{2}|com\.[a-z]{2}|[a-z]{2})$"
)
_SHORT_LINK_HOSTS = frozenset({"maps.app.goo.gl", "goo.gl"})


def extract_data_id(url: str) -> str | None:
    """The SerpApi place id written inside a Google Maps link, or None.

    Works for full place links (".../data=...!1s0x...:0x...") and links with an
    ftid parameter. Short share links have to be followed first (resolve_data_id).
    """
    match = _DATA_ID_IN_LINK.search(unquote(url))
    if match:
        return match.group(1)
    for value in parse_qs(urlsplit(url).query).get("ftid", []):
        if _DATA_ID.fullmatch(value):
            return value
    return None


def resolve_data_id(url: str, client: httpx.Client) -> str:
    """The SerpApi place id for a Google Maps link.

    Short share links (maps.app.goo.gl) are followed one redirect at a time, and
    only to Google hosts, so a link can never make the server call an arbitrary
    address. Links without a place id (searches, ?cid= links) are unsupported.
    """
    data_id = extract_data_id(url)
    if data_id:
        return data_id

    try:
        current = httpx.URL(url)
    except httpx.InvalidURL:
        raise UnsupportedSourceUrlError("The source link is not a valid URL.") from None
    if current.host not in _SHORT_LINK_HOSTS:
        raise UnsupportedSourceUrlError("The source link does not identify a place.")

    for _ in range(MAX_REDIRECTS):
        try:
            response = client.get(current, follow_redirects=False)
        except httpx.TimeoutException:
            raise ReviewFetchTimeoutError(
                "The short link did not answer in time."
            ) from None
        except httpx.HTTPError as error:
            raise UnsupportedSourceUrlError(
                f"Could not open the short link ({type(error).__name__})."
            ) from None
        location = response.headers.get("location")
        if not response.is_redirect or not location:
            break
        current = current.join(location)
        if current.scheme != "https" or not _is_google_host(current.host):
            raise UnsupportedSourceUrlError("The short link leads outside Google Maps.")
        data_id = extract_data_id(str(current))
        if data_id:
            return data_id

    raise UnsupportedSourceUrlError("Could not find the place behind the short link.")


def _is_google_host(host: str) -> bool:
    return host in _SHORT_LINK_HOSTS or bool(_GOOGLE_HOST.match(host))
