// Test factories: build a valid backend object in one line, overriding only the
// fields a test cares about. Every test uses these, so a new field on a type
// is added here once instead of in every test file.
import type { IngestionRun } from "@/lib/ingestion-runs";
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
