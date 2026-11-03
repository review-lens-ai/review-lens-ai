// Test factories: build a valid backend object in one line, overriding only the
// fields a test cares about. Every test uses these, so a new field on a type
// is added here once instead of in every test file.
import type { IngestionRun } from "@/lib/ingestion-runs";
import type { Review, ReviewPage } from "@/lib/reviews";
import type { Target } from "@/lib/targets";

export function ingestionRun(overrides: Partial<IngestionRun> = {}): IngestionRun {
  return {
    id: "run-1",
    target_id: "target-1",
    job_type: "INGESTION",
    status: "COMPLETED",
    queued_at: "2026-10-24T19:09:00Z",
    started_at: "2026-10-24T19:10:00Z",
    finished_at: "2026-10-24T19:12:00Z",
    reviews_ingested: 0,
    reviews_rejected: 0,
    reviews_duplicate: 0,
    error_code: null,
    ...overrides,
  };
}

export function review(overrides: Partial<Review> = {}): Review {
  return {
    id: "review-1",
    target_id: "target-1",
    run_id: "run-1",
    rating: 5,
    review_text: "Great slice, friendly staff.",
    reviewed_at: "2026-10-20T12:00:00Z",
    source_review_id: null,
    source_url: null,
    source_metadata: {},
    created_at: "2026-10-24T19:12:00Z",
    ...overrides,
  };
}

// One page of the paginated reviews endpoint. total defaults to the items given.
export function reviewPage(items: Review[], overrides: Partial<ReviewPage> = {}): ReviewPage {
  return { items, total: items.length, limit: 50, offset: 0, ...overrides };
}

export function target(overrides: Partial<Target> = {}): Target {
  return {
    id: "target-1",
    name: "Joe's Pizza",
    platform: "google_maps",
    source_url: "https://www.google.com/maps/place/Joes+Pizza",
    ingestion_status: null,
    created_at: "2026-10-24T12:00:00Z",
    updated_at: "2026-10-24T12:00:00Z",
    ...overrides,
  };
}
