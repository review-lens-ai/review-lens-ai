// A target's saved reviews, as returned by GET /api/v1/targets/{id}/reviews
// (S1-022, snake_case as sent). Reviewer names are never stored or sent.
import { apiFetch } from "@/lib/api";

export type Review = {
  id: string;
  target_id: string;
  run_id: string;
  rating: number;
  review_text: string;
  reviewed_at: string | null;
  source_review_id: string | null;
  source_url: string | null;
  source_metadata: Record<string, unknown>;
  created_at: string;
};

// How many cards to add each time the user asks for more.
export const REVIEWS_PAGE_SIZE = 4;

const LONG_REVIEW_LENGTH = 280;

export async function fetchReviews(targetId: string): Promise<Review[]> {
  const body = await apiFetch<{ items: Review[] }>(`/api/v1/targets/${targetId}/reviews`);
  return body.items;
}

export function isLongReview(text: string): boolean {
  return text.length > LONG_REVIEW_LENGTH;
}

// e.g. "Oct 12, 2026"
export function formatReviewDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
