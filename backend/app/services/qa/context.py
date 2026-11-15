"""S2-010: the facts about the active target that one question is built from.

    bundle = build_bundle(target, summary, address=address)

The system prompt (prompt.py) and the refusal messages (answer.py) read these
facts: the target's name, platform and address, and the summary of its saved
reviews (S2-008), the same numbers the Summary card shows (S2-BR-007).

Review text is never here. In tools mode it reaches the model only through tool
results; in full mode the orchestrator lists it in the user message. Both read it
again for every question.

Rules:
- OWNERSHIP: target comes from TargetRepository.get_owned and summary from the
  owner-checked summary_for_target, so only the caller's own data is used
  (S2-BR-022, S2-BR-026). owner_id must come from the verified token.
- FRESH EVERY QUESTION: a bundle is made per question and never cached, so
  changing the active target changes everything the model is told (S2-BR-023,
  S2-BR-024).
"""

from dataclasses import dataclass, field

from app.platforms import PLATFORM_LABEL, SUPPORTED_PLATFORM

PLATFORM_LABELS = {SUPPORTED_PLATFORM: PLATFORM_LABEL}


@dataclass(frozen=True)
class ContextBundle:
    target_id: str
    target_name: str
    platform: str
    reviews_total: int
    # The place's address from the provider, to tell this location from others
    # (S2-BR-017); None before the first ingestion or when the provider has none.
    address: str | None = None
    # summary_for_target of all saved reviews (S2-008), for the prompt's facts.
    # None in tests that don't need it.
    summary: dict | None = field(default=None, hash=False, compare=False)

    @property
    def platform_label(self) -> str:
        """The platform as users see it, e.g. "Google Maps"."""
        return PLATFORM_LABELS.get(self.platform, self.platform)


def build_bundle(
    target: dict, summary: dict, *, address: str | None = None
) -> ContextBundle:
    return ContextBundle(
        target_id=str(target["id"]),
        target_name=target["name"],
        platform=target["platform"],
        reviews_total=summary["review_count"],
        address=address,
        summary=summary,
    )
