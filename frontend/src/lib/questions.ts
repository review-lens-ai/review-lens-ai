// S2-009: what the Q&A panel needs besides the request itself.
//
// The request and the answer types come from the generated client
// (src/lib/generated/api.ts: useAskQuestion, AnswerResponse). Only the question is sent;
// the backend never receives earlier turns. Errors use the standard
// { error: { code, message } } body, so ApiError carries them.
import { ApiError } from "@/lib/api";
import { type IngestionStatus, isActive } from "@/lib/ingestion-runs";

// Longest question ReviewLens accepts. Long enough for a specific question,
// short enough that the question never crowds the reviews out of the AI context.
export const QUESTION_MAX_LENGTH = 1000;

// Why the question went unanswered, in words for the user (§6.3). The backend's messages
// are already written for users; anything else (network down, no response) gets our own.
export function askFailureMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 504) return "The AI service didn't respond in time.";
    return error.message;
  }
  return "We couldn't reach ReviewLens. Check your connection.";
}

// Questions to start from when nothing has been asked yet. Each one can be answered
// from reviews alone, for any kind of business.
export const SUGGESTED_QUESTIONS = [
  "What do customers love most?",
  "What are the most common complaints?",
  "What do reviewers mention most often?",
];

// What to tell the user when the target has no reviews to ask about (S2-020): the state of
// its latest ingestion run, so a failed or unfinished run is not mistaken for "never ingested".
export function noReviewsNotice(ingestionStatus: IngestionStatus | null | undefined): {
  title: string;
  message: string;
} {
  if (ingestionStatus === "FAILED") {
    return {
      title: "The last ingestion failed",
      message:
        "No reviews were saved, so there is nothing to ask about yet. Use Ingest reviews above to try again.",
    };
  }
  if (isActive(ingestionStatus)) {
    return {
      title: "Reviews are still being collected",
      message: "Ask your question here once ingestion has finished.",
    };
  }
  return {
    title: "No reviews to ask about yet",
    message:
      "ReviewLens answers only from this target's saved reviews. Use Ingest reviews above to collect them, then ask your question here.",
  };
}
