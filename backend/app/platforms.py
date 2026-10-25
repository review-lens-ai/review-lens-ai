"""The one review platform ReviewLens supports (S1-BR-012) and its URL rules.

ASSUMPTION (needs team confirmation): the platform is Google Maps. To change
platforms, edit only this file; the database stores the platform as plain text,
so no migration is needed.

Validation here answers "does this look like a link to that platform?". It does
NOT check that the page exists or can be ingested; that belongs to ingestion.
"""

import re
from urllib.parse import urlsplit

SUPPORTED_PLATFORM = "google_maps"
PLATFORM_LABEL = "Google Maps"

MAX_URL_LENGTH = 2048

# google.com, google.co.uk, google.com.au ... (a country-specific Google domain)
_GOOGLE_HOST = re.compile(
    r"^(www\.)?google\.(com|co\.[a-z]{2}|com\.[a-z]{2}|[a-z]{2})$"
)


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
    is_google_maps = (
        (
            bool(_GOOGLE_HOST.match(host))
            and (path == "/maps" or path.startswith("/maps/"))
        )
        or host == "maps.google.com"
        or (host == "maps.app.goo.gl" and len(path) > 1)  # share links
        or (host == "goo.gl" and path.startswith("/maps/"))  # older short links
    )
    if not is_google_maps:
        raise ValueError(
            f"This link isn't from {PLATFORM_LABEL}. Open the place on {PLATFORM_LABEL} "
            "and copy its link."
        )
    return url
