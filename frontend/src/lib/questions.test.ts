import { describe, expect, it, vi } from "vitest";

// questions.ts imports the API client, which needs these at import time.
vi.mock("@/lib/api", () => {
  class ApiError extends Error {
    constructor(
      public status: number,
      public code: string,
      message: string,
    ) {
      super(message);
    }
  }
  return { apiFetch: vi.fn(), ApiError };
});

import { ApiError } from "@/lib/api";
import { askFailureMessage, noReviewsNotice } from "./questions";

describe("askFailureMessage", () => {
  it("passes on the backend's user-facing message", () => {
    const error = new ApiError(503, "AI_UNAVAILABLE", "Answers are unavailable right now.");
    expect(askFailureMessage(error)).toBe("Answers are unavailable right now.");
  });

  it("explains a gateway timeout in plain words", () => {
    expect(askFailureMessage(new ApiError(504, "UNKNOWN", "Gateway Timeout"))).toBe(
      "The AI service didn't respond in time.",
    );
  });

  it("never shows a raw network error", () => {
    expect(askFailureMessage(new TypeError("Failed to fetch"))).toBe(
      "We couldn't reach ReviewLens. Check your connection.",
    );
  });
});

describe("noReviewsNotice", () => {
  it("says the last ingestion failed, so it is not mistaken for never ingested", () => {
    expect(noReviewsNotice("FAILED").title).toBe("The last ingestion failed");
  });

  it.each(["QUEUED", "RUNNING"] as const)("says reviews are still coming while %s", (status) => {
    expect(noReviewsNotice(status).title).toBe("Reviews are still being collected");
  });

  it.each([null, undefined, "COMPLETED"] as const)("points to ingestion for %s", (status) => {
    expect(noReviewsNotice(status).title).toBe("No reviews to ask about yet");
  });
});
