// S1-023: an ingestion attempt for a target, and the words the workspace shows for it.
// The types are generated from the backend (npm run api:generate), so they can't drift.
import type { IngestionRunResponse, IngestionRunResponseStatus } from "@/lib/generated/api";

// QUEUED -> RUNNING -> COMPLETED / PARTIAL / FAILED (a queued run can also fail,
// and a failed run can be retried back to QUEUED). Same values as the database enum.
export type IngestionStatus = IngestionRunResponseStatus;
export type IngestionRun = IngestionRunResponse;

// While a run is in one of these states, the workspace keeps checking for updates.
export function isActive(status: IngestionStatus | null | undefined): boolean {
  return status === "QUEUED" || status === "RUNNING";
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
