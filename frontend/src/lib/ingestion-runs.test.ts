import { describe, expect, it, vi } from "vitest";

// These tests cover pure wording/logic only; no network or env needed.
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

import {
  failureMessage,
  isActive,
  reviewCount,
  runDetail,
  type IngestionRun,
} from "./ingestion-runs";

function run(overrides: Partial<IngestionRun>): IngestionRun {
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

describe("runDetail (S1-023 status line)", () => {
  it("says nothing was collected when there is no run", () => {
    expect(runDetail(null)).toBe("No reviews collected yet.");
  });

  it("explains a queued run is waiting", () => {
    expect(runDetail(run({ status: "QUEUED", finished_at: null }))).toBe(
      "Waiting to start collecting reviews.",
    );
  });

  it("uses the singular for one review", () => {
    expect(runDetail(run({ reviews_ingested: 1 }))).toMatch(/^1 review · /);
  });

  it("uses the plural and leaves out skipped when none were skipped", () => {
    const text = runDetail(run({ reviews_ingested: 42 }));
    expect(text).toMatch(/^42 reviews · /);
    expect(text).not.toContain("skipped");
  });

  it("adds the skipped count for a partial run", () => {
    expect(
      runDetail(run({ status: "PARTIAL", reviews_ingested: 38, reviews_rejected: 4 })),
    ).toMatch(/^38 reviews · 4 skipped · /);
  });

  it("never crashes on a status it does not know", () => {
    const unknown = run({ status: "archived" as IngestionRun["status"] });
    expect(runDetail(unknown)).toBe("No reviews collected yet.");
  });
});

describe("isActive", () => {
  it("is true only while a run is queued or running", () => {
    expect(isActive("QUEUED")).toBe(true);
    expect(isActive("RUNNING")).toBe(true);
    expect(isActive("COMPLETED")).toBe(false);
    expect(isActive("PARTIAL")).toBe(false);
    expect(isActive("FAILED")).toBe(false);
    expect(isActive(null)).toBe(false);
  });
});

describe("failureMessage", () => {
  it("maps a known code to its friendly message", () => {
    expect(failureMessage("QUOTA_EXCEEDED").title).toBe(
      "Review collection is temporarily unavailable.",
    );
  });

  it("falls back to a generic message for unknown or missing codes", () => {
    expect(failureMessage("weird_new_code").title).toBe("Something went wrong on our side.");
    expect(failureMessage(null).title).toBe("Something went wrong on our side.");
  });
});

describe("reviewCount", () => {
  it("pluralizes correctly", () => {
    expect(reviewCount(0)).toBe("0 reviews");
    expect(reviewCount(1)).toBe("1 review");
    expect(reviewCount(2)).toBe("2 reviews");
  });
});
