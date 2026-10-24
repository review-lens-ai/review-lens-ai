"""Smallest possible SerpApi client for the google_maps_reviews engine. Standard library only."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

SERPAPI_URL = "https://serpapi.com/search"

# Example place (Gregorys Coffee, NYC) from the serpapi/google-maps-reviews-scraper README.
DEFAULT_DATA_ID = "0x89c259af336b3341:0xa4969e07ce3108de"


def get_api_key():
    key = os.environ.get("SERPAPI_API_KEY", "")
    if not key:
        sys.exit("Set SERPAPI_API_KEY first, e.g. SERPAPI_API_KEY=... python3 <script>")
    return key


def build_params(data_id, sort_by, next_page_token=None):
    """Request parameters without the api_key, so they are safe to save.

    `num` (1-20) is rejected on the first page, so it is only sent with a next_page_token.
    """
    params = {
        "engine": "google_maps_reviews",
        "data_id": data_id,
        "sort_by": sort_by,
        "hl": "en",
        "output": "json",
    }
    if next_page_token:
        params["next_page_token"] = next_page_token
        params["num"] = 20
    return params


def fetch_page(params, api_key):
    """One request = one search credit. Returns (raw response bytes, parsed JSON)."""
    url = f"{SERPAPI_URL}?{urllib.parse.urlencode({**params, 'api_key': api_key})}"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        # SerpApi returns 4xx with a JSON {"error": ...} body (bad key, bad data_id, etc.)
        raw = error.read()

    page = json.loads(raw)
    if "error" in page:
        raise RuntimeError(f"SerpApi error: {page['error']}")

    # Never write the key to disk, even if SerpApi echoes it somewhere.
    return raw.replace(api_key.encode(), b"REDACTED"), page


def next_token(page):
    return page.get("serpapi_pagination", {}).get("next_page_token")
