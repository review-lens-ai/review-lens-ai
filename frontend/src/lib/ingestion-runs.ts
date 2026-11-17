// An ingestion attempt for a target, and the words the workspace shows for it.
// The types are generated from the backend (npm run api:generate), so they can't drift.
import type {
  IngestionRunResponse,
  IngestionRunResponseStatus,
  SkippedReason,
  SkippedReasonReason,
} from "@/lib/generated/api";

// QUEUED -> RUNNING -> COMPLETED / PARTIAL / FAILED (a queued run can also fail,
// and a failed run can be retried back to QUEUED). Same values as the database enum.
export type IngestionStatus = IngestionRunResponseStatus;
export type IngestionRun = IngestionRunResponse;

// While a run is in one of these states, the workspace keeps checking for updates.
export function isActive(status: IngestionStatus | null | undefined): boolean {
  return status === "QUEUED" || status === "RUNNING";
}

// S2-007: what each skip reason means to a person. A review with stars but no words is
// skipped because ReviewLens answers questions from what reviewers wrote.
const SKIP_REASON_LABELS: Record<SkippedReasonReason, string> = {
  MISSING_TEXT: "no written review (star rating only)",
  MISSING_RATING: "no star rating",
  INVALID_RATING: "rating isn't a whole number from 1 to 5",
  NOT_A_RECORD: "not a review record (malformed source data)",
  OTHER: "other reason",
};

// An unknown code (a newer backend) reads as "other reason", never as raw text.
export function skipReasonLabel(reason: string): string {
  return SKIP_REASON_LABELS[reason as SkippedReasonReason] ?? SKIP_REASON_LABELS.OTHER;
}

// e.g. "no written review (star rating only) ×3, no star rating ×1"
export function skippedSummary(reasons: SkippedReason[]): string {
  return reasons.map((r) => `${skipReasonLabel(r.reason)} ×${r.count}`).join(", ");
}

// User-facing failure messages: "what happened" + "what to do next".
// Wording comes from docs/standards/ui-ux-standards.md §6.3. The backend only ever
// sends one of these short codes, never raw error text.
const FAILURE_MESSAGES: Record<string, { title: string; next: string }> = {
  SOURCE_UNREACHABLE: {
    title: "We couldn't reach this page.",
    next: "Check the URL or try again in a few minutes.",
  },
  NO_REVIEWS_FOUND: {
    title: "This page didn't return any reviews.",
    next: "Make sure it's a Google Maps page with reviews.",
  },
  QUOTA_EXCEEDED: {
    title: "Review collection is temporarily unavailable.",
    next: "Try again later.",
  },
  TIMEOUT: {
    title: "Collecting reviews took too long.",
    next: "Try again.",
  },
};

const UNEXPECTED_FAILURE = {
  title: "Something went wrong on our side.",
  next: "Try again, and let the team know if it keeps happening.",
};

export function failureMessage(errorCode: string | null): { title: string; next: string } {
  return (errorCode && FAILURE_MESSAGES[errorCode]) || UNEXPECTED_FAILURE;
}

// S2-007: a run can fetch reviews and still skip every one (no written text, say), so
// "this page didn't return any reviews" would be wrong. Say what happened instead.
export function runFailureMessage(
  run: IngestionRun,
  reasons: SkippedReason[],
): { title: string; next: string } {
  if (run.error_code !== "NO_REVIEWS_FOUND" || run.reviews_rejected === 0) {
    return failureMessage(run.error_code);
  }
  const n = run.reviews_rejected;
  const skipped = `${n} ${n === 1 ? "record was" : "records were"} skipped`;
  const why = reasons.length > 0 ? `: ${skippedSummary(reasons)}` : "";
  return {
    title: "Reviews were found, but none could be used.",
    next: `All ${skipped}${why}. None of them were added to your reviews.`,
  };
}

// e.g. "Oct 24, 3:12 PM"
export function formatRunTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

// "1 review", "42 reviews"
export function reviewCount(n: number): string {
  return `${n} ${n === 1 ? "review" : "reviews"}`;
}

// The detail line next to the status badge (ui-ux-standards.md §7.3).
export function runDetail(run: IngestionRun | null): string {
  if (!run) return "No reviews collected yet.";
  const when = formatRunTime(run.finished_at ?? run.started_at ?? run.queued_at);
  const skipped = run.reviews_rejected > 0 ? ` · ${run.reviews_rejected} skipped` : "";
  switch (run.status) {
    case "QUEUED":
      return "Waiting to start collecting reviews.";
    case "RUNNING":
      return "Collecting reviews from Google Maps. This can take up to a minute.";
    case "COMPLETED":
    case "PARTIAL":
      return `${reviewCount(run.reviews_ingested)}${skipped} · ${when}`;
    case "FAILED":
      return `Latest run failed ${when}`;
    default:
      // A status this version doesn't know yet: show something safe, never crash.
      return "No reviews collected yet.";
  }
}
