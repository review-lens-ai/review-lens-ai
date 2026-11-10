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
import { askFailureMessage, QUESTION_MAX_LENGTH, questionProblem } from "./questions";

describe("questionProblem", () => {
  it("accepts a question", () => {
    expect(questionProblem("What do customers love most?")).toBeNull();
  });

  it("rejects an empty or blank question", () => {
    expect(questionProblem("")).toBe("Enter a question to ask ReviewLens.");
    expect(questionProblem("   \n ")).toBe("Enter a question to ask ReviewLens.");
  });

  it("accepts a question exactly at the limit and rejects one over it", () => {
    expect(questionProblem("x".repeat(QUESTION_MAX_LENGTH))).toBeNull();
    expect(questionProblem("x".repeat(QUESTION_MAX_LENGTH + 1))).toBe(
      "Keep your question under 1,000 characters.",
    );
  });
});

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
