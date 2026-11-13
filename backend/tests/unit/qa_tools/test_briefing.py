from datetime import UTC, datetime

from app.services.qa.tools.briefing import build_briefing

TARGET = {"name": "Smashburger", "platform": "google_maps"}


def _summary(**changes):
    summary = {
        "review_count": 14,
        "average_rating": 2.8,
        "rating_counts": [
            {"rating": 1, "count": 4},
            {"rating": 2, "count": 4},
            {"rating": 3, "count": 2},
            {"rating": 4, "count": 2},
            {"rating": 5, "count": 2},
        ],
        "first_reviewed_at": datetime(2026, 1, 5, tzinfo=UTC),
        "last_reviewed_at": datetime(2026, 4, 2, tzinfo=UTC),
    }
    return summary | changes


def test_the_briefing_gives_the_target_and_the_summary_numbers():
    text = build_briefing(TARGET, _summary())

    assert text.splitlines() == [
        "Active target: Smashburger (Google Maps).",
        "Saved reviews: 14. Average rating: 2.80 out of 5.",
        "By rating: 5 star: 2, 4 star: 2, 3 star: 2, 2 star: 4, 1 star: 4.",
        "Reviewed between 2026-01-05 and 2026-04-02.",
    ]


def test_the_date_line_is_left_out_when_no_review_has_a_date():
    text = build_briefing(
        TARGET, _summary(first_reviewed_at=None, last_reviewed_at=None)
    )

    assert "Reviewed between" not in text


def test_a_target_with_no_reviews_says_so():
    text = build_briefing(TARGET, _summary(review_count=0, average_rating=None))

    assert text == "Active target: Smashburger (Google Maps).\nSaved reviews: none."
