"""Fetch a small version of the per-job sort plan and save every response UNTOUCHED.

RAW means: each page file is the exact response body SerpApi sent. The only change is
the api_key replaced with "REDACTED" if it appears anywhere.

Assumptions:
  - SORT_PLAN is the real per-job plan (25 ratingLow / 20 newestFirst / 5 qualityScore)
    scaled down to 2 pages per sort = 6 credits
  - one place, hl=en, num=20 after page 1, results may be cached up to 1 hour
  - pages stop early if SerpApi returns no next_page_token
  - files contain real reviewer names; run redact_output.py before committing output/

Usage:
    SERPAPI_API_KEY=... python3 spikes/serpapi-reviews/fetch_raw_pages.py [data_id]
Writes:
    output/raw/<sort_by>/page_<n>.json   untouched response bodies
    output/raw/manifest.json             how each page was requested (no key)
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from serpapi_client import DEFAULT_DATA_ID, build_params, fetch_page, get_api_key, next_token

SORT_PLAN = {"ratingLow": 2, "newestFirst": 2, "qualityScore": 2}
RAW_DIR = Path(__file__).resolve().parent / "output" / "raw"


def fetch_sort(data_id, sort_by, max_pages, api_key):
    """Page through one sort order. Returns one manifest entry per saved page."""
    entries = []
    token = None

    for page_number in range(1, max_pages + 1):
        params = build_params(data_id, sort_by, token)
        raw, page = fetch_page(params, api_key)

        path = RAW_DIR / sort_by / f"page_{page_number}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

        next_page_token = next_token(page)
        entries.append({
            "file": str(path.relative_to(RAW_DIR)),
            "sort_by": sort_by,
            "page": page_number,
            "request_params": params,
            "cursor_in": token,
            "cursor_out": next_page_token,
            "search_id": page.get("search_metadata", {}).get("id"),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "review_count": len(page.get("reviews", [])),
        })
        print(f"{sort_by} page {page_number}: {entries[-1]['review_count']} reviews")

        token = next_page_token
        if not token:
            break

    return entries


def main():
    api_key = get_api_key()
    data_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATA_ID

    manifest = []
    for sort_by, max_pages in SORT_PLAN.items():
        manifest += fetch_sort(data_id, sort_by, max_pages, api_key)

    (RAW_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"wrote {len(manifest)} raw pages -> {RAW_DIR}")


if __name__ == "__main__":
    main()
