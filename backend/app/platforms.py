"""The one review platform ReviewLens supports (S1-BR-012) and its URL rules.

ASSUMPTION (needs team confirmation): the platform is Google Maps. To change
platforms, edit only this file; the database stores the platform as plain text,
so no migration is needed.

Validation here answers "does this look like a link to that platform?". It does
NOT check that the page exists or can be ingested; that belongs to ingestion.
"""

import re
from urllib.parse import parse_qs, unquote, urlsplit

SUPPORTED_PLATFORM = "google_maps"
PLATFORM_LABEL = "Google Maps"

MAX_URL_LENGTH = 2048

# google.com, google.co.uk, google.com.au ... (a country-specific Google domain)
_GOOGLE_HOST = re.compile(
    r"^(www\.)?google\.(com|co\.[a-z]{2}|com\.[a-z]{2}|[a-z]{2})$"
)

# The place id SerpApi needs, e.g. 0x89c2537da9b3163d:0x2be30fcff506fec8. Full place
# links carry it after "!1s"; some links carry it in an ftid parameter. The ingestion
# worker (app/adapters/serpapi/place.py) can only use a link that has one, or a short
# share link it can follow, so the form requires the same.
_PLACE_ID = r"0x[0-9a-fA-F]+:0x[0-9a-fA-F]+"
_PLACE_ID_IN_PATH = re.compile(rf"!1s{_PLACE_ID}")


def validate_source_url(raw: str) -> str:
    """Return the cleaned URL or raise ValueError with a message safe to show users."""
    url = raw.strip()
    if not url:
        raise ValueError("Enter the link to the place's Google Maps page.")
    if len(url) > MAX_URL_LENGTH:
        raise ValueError(
            "That link is too long. Paste the link from your browser's address bar."
        )
    if any(ch.isspace() for ch in url):
        raise ValueError("That link contains spaces. Paste it exactly as copied.")
    if not url.isprintable():
        raise ValueError("That doesn't look like a valid link.")

    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port  # raises ValueError for a malformed port
    except ValueError:
        raise ValueError("That doesn't look like a valid link.") from None

    if parts.scheme != "https" or not host:
        raise ValueError("Enter a full link starting with https://")
    if parts.username or parts.password or port not in (None, 443):
        raise ValueError("That doesn't look like a Google Maps link.")

    path = parts.path or "/"
    has_ftid = any(
        re.fullmatch(_PLACE_ID, v) for v in parse_qs(parts.query).get("ftid", [])
    )

    is_google_host = bool(_GOOGLE_HOST.match(host)) or host == "maps.google.com"
    is_short_link = host in ("maps.app.goo.gl", "goo.gl")
    if is_google_host:
        # A place link, not a map view, a search or directions.
        is_place = (
            _has_segment_after(path, "/maps/place/")
            and bool(_PLACE_ID_IN_PATH.search(unquote(url)))
        ) or (path.rstrip("/") in ("", "/maps") and has_ftid)
        is_maps = (
            host == "maps.google.com" or path == "/maps" or path.startswith("/maps/")
        )
    else:
        is_place = (host == "maps.app.goo.gl" and len(path.strip("/")) > 0) or (
            host == "goo.gl" and _has_segment_after(path, "/maps/")
        )
        is_maps = is_short_link and is_place
    if not is_maps:
        raise ValueError(
            f"This link isn't from {PLATFORM_LABEL}. Open the place on {PLATFORM_LABEL} "
            "and copy its link."
        )
    if not is_place:
        raise ValueError(
            "That link doesn't identify one specific place. Open the place on "
            f"{PLATFORM_LABEL} and copy the link from the address bar, or use Share "
            "and copy the short link."
        )
    return url


def _has_segment_after(path: str, prefix: str) -> bool:
    """True when the path starts with prefix and has something after it."""
    return path.startswith(prefix) and len(path[len(prefix) :].strip("/")) > 0
