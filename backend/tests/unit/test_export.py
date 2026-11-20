"""S3-017, S3-018, S3-019: what the export files contain, without a database."""

import csv
import io
from datetime import UTC, datetime

import pytest

from app.services.export import (
    CSV_BYTE_ORDER_MARK,
    REVIEW_CSV_COLUMNS,
    analysis_markdown,
    export_filename,
    reviews_csv,
    slugify,
)

EXPORTED_AT = datetime(2026, 11, 20, 14, 3, tzinfo=UTC)


def _review(text="Great pizza", rating=5, **fields):
    return {
        "rating": rating,
        "review_text": text,
        "reviewed_at": datetime(2026, 3, 12, 9, 30, tzinfo=UTC),
        "source_review_id": "src-1",
        "source_url": "https://example.com/r/1",
        **fields,
    }


def _rows(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text.removeprefix(CSV_BYTE_ORDER_MARK))))


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Blue Bottle - Mint Plaza", "blue-bottle-mint-plaza"),
        ("  Joe's   Pizza!! ", "joe-s-pizza"),
        ("Café Zoë", "cafe-zoe"),
        ("***", "analysis"),
        ("", "analysis"),
    ],
)
def test_the_name_becomes_a_safe_file_name_part(name, slug):
    assert slugify(name) == slug


def test_a_very_long_name_is_cut_without_a_trailing_dash():
    slug = slugify("a" * 59 + " bcd")

    assert len(slug) <= 60
    assert not slug.endswith("-")


def test_file_names_say_what_the_analysis_is_and_what_the_file_holds():
    name = "Blue Bottle Mint Plaza"

    assert export_filename(name, "reviews") == "blue-bottle-mint-plaza-reviews.csv"
    assert export_filename(name, "analysis") == "blue-bottle-mint-plaza-analysis.md"


def test_the_csv_has_one_header_row_and_one_row_per_review():
    rows = _rows(reviews_csv([_review("First"), _review("Second", rating=2)]))

    assert rows[0] == list(REVIEW_CSV_COLUMNS)
    assert [r[1] for r in rows[1:]] == ["First", "Second"]
    assert rows[2][0] == "2"


def test_the_csv_never_invents_a_value_for_missing_fields():
    review = _review(reviewed_at=None, source_review_id=None, source_url=None)

    assert _rows(reviews_csv([review]))[1][2:] == ["", "", ""]


def test_the_csv_has_no_reviewer_name_column():
    assert not {"name", "reviewer", "author", "display_name"} & set(REVIEW_CSV_COLUMNS)


def test_dates_are_iso_8601():
    assert _rows(reviews_csv([_review()]))[1][2] == "2026-03-12T09:30:00+00:00"


def test_commas_quotes_and_line_breaks_stay_in_one_cell():
    text = 'Loved it, "really"\nwould return'

    assert _rows(reviews_csv([_review(text)]))[1][1] == text


def test_text_a_spreadsheet_would_run_as_a_formula_stays_plain_text():
    row = _rows(reviews_csv([_review('=HYPERLINK("http://x")')]))[1]

    assert row[1].startswith("'=HYPERLINK")


def test_the_csv_starts_with_a_byte_order_mark_so_excel_reads_utf8():
    assert reviews_csv([]).startswith(CSV_BYTE_ORDER_MARK)


def _summary(count=14):
    counts = [6, 2, 1, 1, 4]
    return {
        "review_count": count,
        "average_rating": 2.79,
        "rating_counts": [{"rating": n, "count": c} for n, c in enumerate(counts, 1)],
        "first_reviewed_at": datetime(2017, 11, 17, tzinfo=UTC),
        "last_reviewed_at": datetime(2026, 9, 22, tzinfo=UTC),
    }


def _target(name="Smashburger"):
    return {
        "name": name,
        "source_url": "https://maps.app.goo.gl/abc",
        "created_at": datetime(2026, 11, 3, 18, 0, tzinfo=UTC),
    }


def _run(**fields):
    return {
        "status": "PARTIAL",
        "finished_at": datetime(2026, 11, 3, 18, 1, tzinfo=UTC),
        "reviews_ingested": 14,
        "reviews_rejected": 12,
        "reviews_duplicate": 0,
        **fields,
    }


def test_the_markdown_names_the_analysis_and_says_when_it_was_exported():
    text = analysis_markdown(_target(), _summary(), _run(), [], EXPORTED_AT)

    assert text.startswith("# Smashburger\n")
    assert "Exported from ReviewLens on 2026-11-20 14:03 UTC." in text


def test_the_markdown_gives_the_source_and_the_dataset_summary():
    text = analysis_markdown(_target(), _summary(), _run(), [], EXPORTED_AT)

    assert "- Platform: Google Maps" in text
    assert "- Source link: <https://maps.app.goo.gl/abc>" in text
    assert "- Saved reviews: 14" in text
    assert "- Average rating: 2.79 out of 5" in text
    assert "- Reviewed between 2017-11-17 and 2026-09-22" in text
    assert "| 5 | 4 |" in text


def test_the_markdown_reports_the_last_ingestion_with_skipped_reasons():
    skipped = [{"reason": "MISSING_TEXT", "count": 12}]

    text = analysis_markdown(_target(), _summary(), _run(), skipped, EXPORTED_AT)

    assert "- Status: Partially ingested" in text
    assert "- Records skipped: 12" in text
    assert "  - no written review (star rating only): 12" in text


def test_an_analysis_that_was_never_ingested_says_so_and_has_no_numbers():
    empty = {**_summary(0), "average_rating": None, "first_reviewed_at": None}

    text = analysis_markdown(_target(), empty, None, [], EXPORTED_AT)

    assert "- Saved reviews: 0" in text
    assert "Average rating" not in text
    assert "has not been ingested yet" in text


def test_a_name_cannot_start_markdown_or_html_of_its_own():
    target = _target("# Hacked <script>[x](y)")

    text = analysis_markdown(target, _summary(), _run(), [], EXPORTED_AT)

    assert text.splitlines()[0] == r"# \# Hacked \<script\>\[x\]\(y\)"
