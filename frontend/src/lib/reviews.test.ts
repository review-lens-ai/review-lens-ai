import { describe, expect, it, vi } from "vitest";

// reviews.ts imports the API client, which needs these at import time.
vi.mock("@/lib/api", () => ({ apiFetch: vi.fn() }));

import { isLongReview } from "./reviews";

describe("isLongReview", () => {
  it("leaves short reviews untruncated", () => {
    expect(isLongReview("Great slice")).toBe(false);
  });

  it("truncates reviews over the limit", () => {
    expect(isLongReview("x".repeat(281))).toBe(true);
  });
});
