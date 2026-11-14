// S2-009: asking ReviewLens a question about the active target's reviews.
//
// Contract (backend/app/schemas/qa.py, S2-003; the endpoint is built in S2-010/S2-011):
//   POST /api/v1/targets/{id}/questions  { "question": "..." }  ->  AnswerResponse
// Only the question is sent. Earlier turns, once they exist, are loaded by the backend
// from its own storage, never sent by the browser.
// Errors use the standard { error: { code, message } } body, so ApiError carries them.
//
// The types below copy qa.py by hand until the endpoint is in openapi.json; then they
// are replaced by the generated ones (src/lib/generated/api.ts).
import { apiFetch, ApiError } from "@/lib/api";

// Longest question ReviewLens accepts. Long enough for a specific question,
// short enough that the question never crowds the reviews out of the AI context.
export const QUESTION_MAX_LENGTH = 1000;

export type AnswerStatus = "ANSWERED" | "INSUFFICIENT_EVIDENCE" | "OUT_OF_SCOPE" | "NO_REVIEWS";

// A review the answer is based on, copied from the dataset (never from model text).
export type EvidenceReview = {
  id: string;
  rating: number;
  review_text: string;
  reviewed_at: string | null;
};

// Which dataset answered, with counts from the database.
export type AnswerContext = {
  target_id: string;
  target_name: string;
  platform: string;
  reviews_considered: number;
  reviews_total: number;
  is_subset: boolean;
};

export type AnswerResponse = {
  status: AnswerStatus;
  answer: string;
  evidence: EvidenceReview[];
  context: AnswerContext;
};

export async function askQuestion(targetId: string, question: string): Promise<AnswerResponse> {
  return apiFetch<AnswerResponse>(`/api/v1/targets/${targetId}/questions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
}

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
