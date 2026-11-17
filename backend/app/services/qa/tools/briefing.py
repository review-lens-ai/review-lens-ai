"""The facts the model starts every question with (S2-010).

    build_briefing(target, summary) -> str

target is the user's active target (name, platform). summary is what
ReviewRepository.summary_for_target returns, the same numbers the summary card
shows (S2-BR-007). With the basics already in front of it, the model needs no tool
call to say how many reviews there are or what the average is, and its numbers
cannot disagree with the screen.

The text is added to the system prompt by the orchestrator; it holds no rules.
"""

_PLATFORM_NAMES = {"google_maps": "Google Maps"}


def _platform(platform: str) -> str:
    return _PLATFORM_NAMES.get(platform, platform.replace("_", " ").title())


def build_briefing(target: dict, summary: dict) -> str:
    header = f"Active target: {target['name']} ({_platform(target['platform'])})."
    total = summary["review_count"]
    if total == 0:
        return f"{header}\nSaved reviews: none."
    counts = {item["rating"]: item["count"] for item in summary["rating_counts"]}
    by_rating = ", ".join(f"{n} star: {counts[n]}" for n in range(5, 0, -1))
    lines = [
        header,
        f"Saved reviews: {total}. Average rating: {summary['average_rating']:.2f} out of 5.",
        f"By rating: {by_rating}.",
    ]
    first, last = summary["first_reviewed_at"], summary["last_reviewed_at"]
    if first and last:
        lines.append(f"Reviewed between {first:%Y-%m-%d} and {last:%Y-%m-%d}.")
    return "\n".join(lines)
