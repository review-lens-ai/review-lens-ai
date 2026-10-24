"""Replace reviewer identities in output/raw/ and output/merged_sample.json with stable fakes.

Run after fetching and before committing output/. The JSON shape stays exactly the same;
only identifying values change. The same reviewer gets the same fake in every file.

Redacted:
  - user.name, user.link, user.thumbnail, user.contributor_id  -> "Reviewer 01", example.com links, "contributor_01"
  - images                                                     -> example.com placeholders (photos can show people)
  - the reviewer's name inside the owner reply (response)      -> "Reviewer 01"
  - search_metadata links to SerpApi's stored HTML/JSON        -> "REDACTED" (that HTML holds the real names)
Not redacted: names a reviewer writes inside their own review text.

Usage:
    python3 spikes/serpapi-reviews/redact_output.py
"""

import json
import re
from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent / "output"
SEARCH_METADATA_LINKS = ["json_endpoint", "markdown_endpoint", "raw_html_file", "prettify_html_file"]

fakes = {}  # contributor_id -> number, shared across files


def fake_number(user):
    key = user.get("contributor_id") or user.get("name")
    if key not in fakes:
        fakes[key] = len(fakes) + 1
    return fakes[key]


def redact_reply(response, real_name, fake_name):
    """Owner replies usually start with "Hi <reviewer name>"."""
    if not real_name:
        return
    # Whole words only, so a one-letter name like "A" does not replace every "A" in the reply.
    pattern = re.compile(r"(?<!\w)" + re.escape(real_name) + r"(?!\w)")
    if "snippet" in response:
        response["snippet"] = pattern.sub(fake_name, response["snippet"])
    for key, text in response.get("extracted_snippet", {}).items():
        response["extracted_snippet"][key] = pattern.sub(fake_name, text)


def redact_review(review):
    user = review.get("user", {})
    n = fake_number(user)
    real_name = user.get("name")
    fake_name = f"Reviewer {n:02d}"

    if "name" in user:
        user["name"] = fake_name
    if "link" in user:
        user["link"] = f"https://example.com/contrib/{n:02d}"
    if "thumbnail" in user:
        user["thumbnail"] = f"https://example.com/avatar/{n:02d}.jpg"
    if "contributor_id" in user:
        user["contributor_id"] = f"contributor_{n:02d}"

    if "images" in review:
        review["images"] = [f"https://example.com/photo/{n:02d}_{i}.jpg" for i in range(len(review["images"]))]
    if "response" in review:
        redact_reply(review["response"], real_name, fake_name)


def redact_file(path):
    data = json.loads(path.read_text())
    for review in data.get("reviews", []):
        redact_review(review)
    for link in SEARCH_METADATA_LINKS:
        if link in data.get("search_metadata", {}):
            data["search_metadata"][link] = "REDACTED"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def main():
    files = sorted((OUTPUT_DIR / "raw").glob("*/page_*.json")) + [OUTPUT_DIR / "merged_sample.json"]
    for path in files:
        if path.exists():
            redact_file(path)
            print(f"redacted {path.relative_to(OUTPUT_DIR)}")
    print(f"{len(fakes)} reviewers replaced")


if __name__ == "__main__":
    main()
