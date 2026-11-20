"""S3-017, S3-018, S3-019: the files a user can download for one analysis (target).

    export_filename(name, kind)    "blue-bottle-mint-plaza-reviews.csv"
    reviews_csv(reviews)           the saved review dataset as CSV text
    analysis_markdown(...)         the saved analysis as readable Markdown

kind is "reviews" (the CSV) or "analysis" (the Markdown). The same two words are the
last part of the download URL and of the file name, and the frontend uses them too
(frontend/src/lib/exports.ts), so one name means one thing everywhere.

Only data that exists is exported. The canonical Review has no reviewer name, so the
CSV has none; empty values stay empty and nothing is invented to fill a column
(S3-017). The caller reads the data through its owner-checked repositories, so a user
can only export their own analyses (S3-BR-026, S3-BR-030).
"""

import csv
import io
import re
import unicodedata
from datetime import UTC, datetime
from typing import Literal

from app.platforms import PLATFORM_LABEL

ExportKind = Literal["reviews", "analysis"]
EXPORT_EXTENSIONS: dict[ExportKind, str] = {"reviews": "csv", "analysis": "md"}

# The Review fields a user can use outside ReviewLens, in this order.
REVIEW_CSV_COLUMNS = (
    "rating",
    "review_text",
    "reviewed_at",
    "source_review_id",
    "source_url",
)

MAX_SLUG_LENGTH = 60
# Lets Excel read the file as UTF-8, so accents and emoji in reviews survive.
CSV_BYTE_ORDER_MARK = "﻿"
# A spreadsheet runs a cell that starts with one of these as a formula. Review text is
# written by strangers, so such a cell gets a leading apostrophe and stays plain text.
_FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

# The same words as the status badge in the workspace.
INGESTION_STATUS_TEXT = {
    "QUEUED": "Queued",
    "RUNNING": "Ingesting",
    "COMPLETED": "Ingested",
    "PARTIAL": "Partially ingested",
    "FAILED": "Failed",
}
# The same words as skipReasonLabel in frontend/src/lib/ingestion-runs.ts.
SKIP_REASON_TEXT = {
    "MISSING_TEXT": "no written review (star rating only)",
    "MISSING_RATING": "no star rating",
    "INVALID_RATING": "rating isn't a whole number from 1 to 5",
    "NOT_A_RECORD": "not a review record (malformed source data)",
    "OTHER": "other reason",
}


def slugify(name: str) -> str:
    """ "Blue Bottle - Mint Plaza" -> "blue-bottle-mint-plaza"; "analysis" if nothing
    usable is left (a name of only symbols)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return slug[:MAX_SLUG_LENGTH].strip("-") or "analysis"


def export_filename(target_name: str, kind: ExportKind) -> str:
    return f"{slugify(target_name)}-{kind}.{EXPORT_EXTENSIONS[kind]}"


def reviews_csv(reviews: list[dict]) -> str:
    """One row per saved review, newest first as given. Dates are ISO 8601 (UTC)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(REVIEW_CSV_COLUMNS)
    for review in reviews:
        writer.writerow(
            [
                review["rating"],
                _cell(review["review_text"]),
                _iso(review["reviewed_at"]),
                _cell(review["source_review_id"]),
                _cell(review["source_url"]),
            ]
        )
    return CSV_BYTE_ORDER_MARK + out.getvalue()


def analysis_markdown(
    target: dict,
    summary: dict,
    run: dict | None,
    skipped_reasons: list[dict],
    exported_at: datetime,
) -> str:
    """The saved analysis for a person to read: where the reviews come from, what the
    saved dataset looks like, and how the last ingestion went. summary, run and
    skipped_reasons are what summary_for_target, latest_for_target and
    skipped_reasons return. Q&A history is not saved yet, so it has no section."""
    lines = [
        f"# {_text(target['name'])}",
        "",
        f"Exported from ReviewLens on {_stamp(exported_at)}.",
        "",
        "## Source",
        "",
        f"- Platform: {PLATFORM_LABEL}",
        f"- Source link: <{target['source_url']}>",
        f"- Analysis created: {_stamp(target['created_at'])}",
        "",
        "## Review dataset",
        "",
        f"- Saved reviews: {summary['review_count']}",
    ]
    if summary["review_count"] > 0:
        lines.append(f"- Average rating: {summary['average_rating']:.2f} out of 5")
        if summary["first_reviewed_at"] and summary["last_reviewed_at"]:
            lines.append(
                f"- Reviewed between {summary['first_reviewed_at']:%Y-%m-%d}"
                f" and {summary['last_reviewed_at']:%Y-%m-%d}"
            )
        lines += ["", "| Stars | Reviews |", "|---|---|"]
        for item in sorted(summary["rating_counts"], key=lambda r: -r["rating"]):
            lines.append(f"| {item['rating']} | {item['count']} |")
    lines += ["", "## Last ingestion", ""]
    lines += _ingestion_lines(run, skipped_reasons)
    return "\n".join(lines) + "\n"


def _ingestion_lines(run: dict | None, skipped_reasons: list[dict]) -> list[str]:
    if run is None:
        return ["- This analysis has not been ingested yet."]
    lines = [f"- Status: {INGESTION_STATUS_TEXT.get(run['status'], run['status'])}"]
    if run["finished_at"]:
        lines.append(f"- Finished: {_stamp(run['finished_at'])}")
    lines += [
        f"- Reviews saved: {run['reviews_ingested']}",
        f"- Records skipped: {run['reviews_rejected']}",
        f"- Duplicates left out: {run['reviews_duplicate']}",
    ]
    for reason in skipped_reasons:
        text = SKIP_REASON_TEXT.get(reason["reason"], SKIP_REASON_TEXT["OTHER"])
        lines.append(f"  - {text}: {reason['count']}")
    return lines


def _cell(value: str | None) -> str:
    if value is None:
        return ""
    return "'" + value if value.startswith(_FORMULA_STARTS) else value


def _iso(value: datetime | None) -> str:
    return "" if value is None else value.isoformat()


def _stamp(value: datetime) -> str:
    return f"{value.astimezone(UTC):%Y-%m-%d %H:%M} UTC"


def _text(value: str) -> str:
    """A name as one line of Markdown text, so it can't start a list, a link or HTML."""
    one_line = " ".join(value.split())
    return re.sub(r"([\\`*_{}\[\]<>()#+!|~-])", r"\\\1", one_line)
