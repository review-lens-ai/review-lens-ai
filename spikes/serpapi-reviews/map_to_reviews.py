"""Map the raw SerpApi pages to the canonical schema (README §5 and §6). No credits used.

This is the mapping half of the SerpApi adapter only: no database, no checkpoints, no retries.

The two output files are a PAIR, one per table: every review row belongs to the target row
through target_id -> id (the analysis_targets 1:N reviews foreign key). The id is a placeholder;
the database generates real uuids, plus ingestion_run_id and created_at.

Usage:
    python3 spikes/serpapi-reviews/map_to_reviews.py     (run fetch_raw_pages.py first)
Reads:
    output/raw/manifest.json + page files
Writes:
    output/canonical/analysis_target.json   one analysis_targets row
    output/canonical/reviews.json           its reviews rows, deduplicated on source_review_id
"""

import json
from collections import Counter
from pathlib import Path

MAPPING_VERSION = "serpapi@1"

RAW_DIR = Path(__file__).resolve().parent / "output" / "raw"
CANONICAL_DIR = Path(__file__).resolve().parent / "output" / "canonical"

# Placeholder for the database-generated uuid; links the two output files.
TARGET_ID = "example-target-id"
# Placeholder for the name the user types when submitting; ingestion never overwrites it.
TARGET_NAME = "example name entered by user"

# Every review field must be listed here, so a new SerpApi field is noticed, not silently lost.
MAPPED_FIELDS = {"review_id", "snippet", "rating", "iso_date", "link",
                 "iso_date_of_last_edit", "likes", "details", "user"}
DROPPED_FIELDS = {
    "extracted_snippet",  # same text as snippet
    "images",             # can show people
    "response",           # owner reply addresses the reviewer by name
    "position",           # restarts every page
    "date",               # relative ("2 weeks ago")
    "source",             # always "Google"
}
# From `user` we keep only stats that do not identify anyone.
# Dropped: name, link, thumbnail, contributor_id.
KEPT_USER_FIELDS = {"local_guide", "reviews", "photos"}


def map_target(page):
    """place_info + topics only appear on page 1 of a sort."""
    place = page["place_info"]
    return {
        "id": TARGET_ID,
        "name": TARGET_NAME,
        "platform": "google_maps",
        "source_ref": page["search_parameters"]["data_id"],
        "source_url": page["search_metadata"].get("google_maps_reviews_url"),
        "source_metadata": {
            "provider_name": place.get("title"),
            "address": place.get("address"),
            "category": place.get("type"),
            "provider_rating": place.get("rating"),
            "provider_review_count": place.get("reviews"),
            "topics": page.get("topics", []),
        },
    }


def map_review(raw):
    """Return (row, None) or (None, skip_reason)."""
    if not raw.get("review_id"):
        return None, "no review_id"
    if not raw.get("snippet"):
        return None, "no text"

    user = raw.get("user", {})
    rating = raw.get("rating")

    return {
        "target_id": TARGET_ID,
        "source_review_id": raw["review_id"],
        "content": raw["snippet"],
        "rating": rating,
        "rating_scale": 5 if rating is not None else None,
        "published_at": raw.get("iso_date"),
        "source_url": raw.get("link"),
        "source_metadata": {
            "edited_at": raw.get("iso_date_of_last_edit"),
            "likes": raw.get("likes"),
            "details": raw.get("details", {}),
            "reviewer": {k: v for k, v in user.items() if k in KEPT_USER_FIELDS},
        },
        "mapping_version": MAPPING_VERSION,
    }, None


def main():
    manifest = json.loads((RAW_DIR / "manifest.json").read_text())

    target = None
    reviews = {}            # source_review_id -> row; first seen wins, like the upsert
    skipped = Counter()
    unknown_fields = Counter()
    duplicates = 0

    for entry in manifest:
        page = json.loads((RAW_DIR / entry["file"]).read_text())
        if target is None and "place_info" in page:
            target = map_target(page)

        for raw in page.get("reviews", []):
            unknown_fields.update(set(raw) - MAPPED_FIELDS - DROPPED_FIELDS)

            row, reason = map_review(raw)
            if reason:
                skipped[reason] += 1
            elif row["source_review_id"] in reviews:
                duplicates += 1
            else:
                reviews[row["source_review_id"]] = row

    CANONICAL_DIR.mkdir(exist_ok=True)
    (CANONICAL_DIR / "analysis_target.json").write_text(json.dumps(target, indent=2, ensure_ascii=False))
    (CANONICAL_DIR / "reviews.json").write_text(json.dumps(list(reviews.values()), indent=2, ensure_ascii=False))

    print(f"wrote pair -> {CANONICAL_DIR}: 1 analysis_target + {len(reviews)} reviews (linked by target_id)")
    print(f"duplicates across sorts: {duplicates}")
    print(f"skipped (reviews_failed): {dict(skipped)}")
    print(f"unmapped SerpApi fields: {dict(unknown_fields) or 'none'}")


if __name__ == "__main__":
    main()
