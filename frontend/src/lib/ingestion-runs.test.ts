import { describe, expect, it, vi } from "vitest";

// These tests cover pure wording/logic only; no network or env needed.
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

import { apiFetch } from "@/lib/api";
import {
  failureMessage,
  fetchLatestRun,
  isActive,
  reviewCount,
  runDetail,
  runFailureMessage,
  skippedSummary,
  skipReasonLabel,
  startIngestionRun,
  type IngestionRun,
} from "./ingestion-runs";
import { ingestionRun } from "@/test/factories";

describe("runDetail (S1-023 status line)", () => {
  it("says nothing was collected when there is no run", () => {
    expect(runDetail(null)).toBe("No reviews collected yet.");
  });

  it("explains a queued run is waiting", () => {
    expect(runDetail(ingestionRun({ status: "QUEUED", finished_at: null }))).toBe(
      "Waiting to start collecting reviews.",
    );
  });

  it("uses the singular for one review", () => {
    expect(runDetail(ingestionRun({ reviews_ingested: 1 }))).toMatch(/^1 review · /);
  });

  it("uses the plural and leaves out skipped when none were skipped", () => {
    const text = runDetail(ingestionRun({ reviews_ingested: 42 }));
    expect(text).toMatch(/^42 reviews · /);
    expect(text).not.toContain("skipped");
  });

  it("adds the skipped count for a partial run", () => {
    expect(
      runDetail(ingestionRun({ status: "PARTIAL", reviews_ingested: 38, reviews_rejected: 4 })),
    ).toMatch(/^38 reviews · 4 skipped · /);
  });

  it("never crashes on a status it does not know", () => {
    const unknown = ingestionRun({ status: "archived" as IngestionRun["status"] });
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

describe("startIngestionRun (S1-019)", () => {
  it("POSTs to the target's ingestion-runs and returns the queued run", async () => {
    const queued = ingestionRun({ status: "QUEUED", started_at: null, finished_at: null });
    vi.mocked(apiFetch).mockResolvedValueOnce(queued);

    await expect(startIngestionRun("target-1")).resolves.toEqual(queued);
    expect(apiFetch).toHaveBeenCalledWith("/api/v1/targets/target-1/ingestion-runs", {
      method: "POST",
    });
  });

  it("passes a refusal (e.g. 409, a run is already active) on to the page", async () => {
    const conflict = Object.assign(new Error("Reviews are already being collected"), {
      status: 409,
    });
    vi.mocked(apiFetch).mockRejectedValueOnce(conflict);

    await expect(startIngestionRun("target-1")).rejects.toBe(conflict);
  });
});

describe("skipReasonLabel (S2-007)", () => {
  it("says a star-rating-only review has no written review", () => {
    expect(skipReasonLabel("MISSING_TEXT")).toBe("no written review (star rating only)");
  });

  it("gives every reason its own specific wording", () => {
    const codes = ["MISSING_TEXT", "MISSING_RATING", "INVALID_RATING", "NOT_A_RECORD", "OTHER"];
    const labels = codes.map(skipReasonLabel);
    expect(new Set(labels).size).toBe(codes.length);
  });

  it("shows an unknown code as 'other reason', never as raw text", () => {
    expect(skipReasonLabel("SOMETHING_NEW")).toBe("other reason");
    expect(skipReasonLabel("")).toBe("other reason");
  });
});

describe("skippedSummary (S2-007)", () => {
  it("lists each reason with its count", () => {
    expect(
      skippedSummary([
        { reason: "MISSING_TEXT", count: 3 },
        { reason: "MISSING_RATING", count: 1 },
      ]),
    ).toBe("no written review (star rating only) ×3, no star rating ×1");
  });

  it("is empty when nothing was skipped", () => {
    expect(skippedSummary([])).toBe("");
  });
});

describe("runFailureMessage (S2-007)", () => {
  const skippedAll = ingestionRun({
    status: "FAILED",
    error_code: "NO_REVIEWS_FOUND",
    reviews_rejected: 2,
  });

  it("says reviews were found but none could be used, when every record was skipped", () => {
    const message = runFailureMessage(skippedAll, [{ reason: "MISSING_TEXT", count: 2 }]);
    expect(message.title).toBe("Reviews were found, but none could be used.");
    expect(message.next).toBe(
      "All 2 records were skipped: no written review (star rating only) ×2. " +
        "None of them were added to your reviews.",
    );
  });

  it("uses the singular for one skipped record", () => {
    const one = { ...skippedAll, reviews_rejected: 1 };
    expect(runFailureMessage(one, []).next).toContain("All 1 record was skipped.");
  });

  it("still gives the old message when the page really returned nothing", () => {
    const empty = { ...skippedAll, reviews_rejected: 0 };
    expect(runFailureMessage(empty, [])).toEqual(failureMessage("NO_REVIEWS_FOUND"));
  });

  it("leaves every other failure message alone, even if records were skipped", () => {
    const timeout = { ...skippedAll, error_code: "TIMEOUT" };
    expect(runFailureMessage(timeout, [])).toEqual(failureMessage("TIMEOUT"));
  });
});

describe("fetchLatestRun (S2-007)", () => {
  it("returns the run together with why it skipped records", async () => {
    const run = ingestionRun({ status: "PARTIAL", reviews_rejected: 3 });
    const reasons = [{ reason: "MISSING_TEXT", count: 3 }];
    vi.mocked(apiFetch).mockResolvedValueOnce({ run, skipped_reasons: reasons });

    expect(await fetchLatestRun("t1")).toEqual({ run, skipped_reasons: reasons });
  });

  it("treats a response without reasons as none skipped", async () => {
    vi.mocked(apiFetch).mockResolvedValueOnce({ run: null });

    expect(await fetchLatestRun("t1")).toEqual({ run: null, skipped_reasons: [] });
  });
});
