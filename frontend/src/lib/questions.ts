// S2-009: asking ReviewLens a question about the active target's reviews.
//
// Contract (the backend endpoint is built in S2-010/S2-011):
//   POST /api/v1/targets/{id}/questions  { "question": "..." }  ->  { "answer": "..." }
// Errors use the standard { error: { code, message } } body, so ApiError carries them.
import { apiFetch, ApiError } from "@/lib/api";

// Longest question the box accepts. Long enough for a specific question,
// short enough that the question never crowds the reviews out of the AI context.
export const QUESTION_MAX_LENGTH = 1000;

export type QuestionAnswer = { answer: string };

export async function askQuestion(targetId: string, question: string): Promise<QuestionAnswer> {
  return apiFetch<QuestionAnswer>(`/api/v1/targets/${targetId}/questions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question }),
  });
}

// What is wrong with a question before it is sent, or null when it can be sent.
// The backend checks again and is authoritative (ui-ux-standards.md §6.2).
export function questionProblem(question: string): string | null {
  if (question.trim() === "") return "Enter a question to ask ReviewLens.";
  if (question.length > QUESTION_MAX_LENGTH) {
    return `Keep your question under ${QUESTION_MAX_LENGTH.toLocaleString()} characters.`;
  }
  return null;
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

// Questions to start from when the box is empty (mockup: "Your next insight starts with a question").
export const SUGGESTED_QUESTIONS = [
  "What do customers love most?",
  "What are the most common complaints?",
  "What should we improve first?",
];
