// S1-023: the latest ingestion attempt for a target, as returned by
// GET /api/v1/targets/{id}/ingestion-runs/latest (snake_case, as sent).
import { apiFetch } from "@/lib/api";

export type IngestionStatus = "running" | "succeeded" | "partial" | "failed";

export type IngestionRun = {
  id: string;
  target_id: string;
  status: IngestionStatus;
  started_at: string;
  finished_at: string | null;
  reviews_ingested: number;
  reviews_rejected: number;
  error_code: string | null;
};

export async function fetchLatestRun(targetId: string): Promise<IngestionRun | null> {
  const body = await apiFetch<{ run: IngestionRun | null }>(
    `/api/v1/targets/${targetId}/ingestion-runs/latest`,
  );
  return body.run;
}

// User-facing failure messages: "what happened" + "what to do next".
// Wording comes from docs/standards/ui-ux-standards.md §6.3. The backend only ever
// sends one of these short codes, never raw error text.
const FAILURE_MESSAGES: Record<string, { title: string; next: string }> = {
  source_unreachable: {
    title: "We couldn't reach this page.",
    next: "Check the URL or try again in a few minutes.",
  },
  no_reviews_found: {
    title: "This page didn't return any reviews.",
    next: "Make sure it's a Google Maps page with reviews.",
  },
  quota_exceeded: {
    title: "Review collection is temporarily unavailable.",
    next: "Try again later.",
  },
  timeout: {
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

// The detail line next to the status badge (ui-ux-standards.md §7.3).
export function runDetail(run: IngestionRun | null): string {
  if (!run) return "No reviews collected yet.";
  switch (run.status) {
    case "running":
      return "Collecting reviews from Google Maps. This can take up to a minute.";
    case "succeeded":
      return `${run.reviews_ingested} reviews · ${formatRunTime(run.finished_at ?? run.started_at)}`;
    case "partial":
      return `${run.reviews_ingested} reviews saved, ${run.reviews_rejected} skipped.`;
    case "failed":
      return `Latest run failed ${formatRunTime(run.finished_at ?? run.started_at)}`;
  }
}
