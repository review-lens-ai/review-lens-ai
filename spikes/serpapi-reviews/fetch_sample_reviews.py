"""Fetch a few pages of one sort order and MERGE them into one convenience file.

THIS OUTPUT IS NOT RAW. Assumptions baked in by the merge:
  - place_info, topics and search_parameters are taken from page 1 only
  - search_metadata and serpapi_pagination are dropped (no search id, no cursors)
  - page boundaries are lost; `position` restarts at 1 on every page
  - `data_id` at the top level is added by this script, not returned by SerpApi
  - one place, one sort order (newestFirst), hl=en, results may be cached up to 1 hour
For the untouched responses, use fetch_raw_pages.py.

Usage:
    SERPAPI_API_KEY=... python3 spikes/serpapi-reviews/fetch_sample_reviews.py [data_id] [max_pages]
Writes output/merged_sample.json. Costs max_pages credits (default 3).
"""

import json
import sys
from pathlib import Path

from serpapi_client import DEFAULT_DATA_ID, build_params, fetch_page, get_api_key, next_token

SORT_BY = "newestFirst"
DEFAULT_MAX_PAGES = 3
OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "merged_sample.json"


def fetch_pages(data_id, max_pages, api_key):
    """Follow next_page_token until there are no more pages or max_pages is reached."""
    pages = []
    token = None

    for _ in range(max_pages):
        _, page = fetch_page(build_params(data_id, SORT_BY, token), api_key)
        pages.append(page)
        print(f"page {len(pages)}: {len(page.get('reviews', []))} reviews")

        token = next_token(page)
        if not token:
            break

    return pages


# Written at the top of the output so the file explains itself when opened on its own.
ABOUT = {
    "what": "MERGED, NOT RAW. SerpApi's own shape, NOT the end-product schema.",
    "raw_instead": "output/raw/ (fetch_raw_pages.py)",
    "end_product_instead": "output/canonical/ (map_to_reviews.py)",
    "assumptions": [
        "place_info, topics and search_parameters come from page 1 only",
        "search_metadata and serpapi_pagination are dropped (no search id, no cursors)",
        "page boundaries are lost; reviews[].position restarts at 1 on every page",
        "data_id at the top level is added by this script, not returned by SerpApi",
        f"one place, one sort order ({SORT_BY}), hl=en, results may be cached up to 1 hour",
        "reviewer identities replaced with fakes by redact_output.py before committing",
    ],
}


def merge_pages(data_id, pages):
    first = pages[0]
    return {
        "_about": ABOUT,
        "data_id": data_id,
        "search_parameters": first.get("search_parameters"),
        "place_info": first.get("place_info"),
        "topics": first.get("topics"),
        "reviews": [review for page in pages for review in page.get("reviews", [])],
    }


def main():
    api_key = get_api_key()
    data_id = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DATA_ID
    max_pages = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_PAGES

    merged = merge_pages(data_id, fetch_pages(data_id, max_pages, api_key))

    OUTPUT_PATH.parent.mkdir(exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(merged, indent=2, ensure_ascii=False))
    print(f"wrote {len(merged['reviews'])} reviews -> {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
