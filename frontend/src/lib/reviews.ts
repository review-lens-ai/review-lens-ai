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

// One page of a target's reviews. The endpoint is paginated (coding-standards.md §5):
// items is one page, not everything, and total counts all of the target's reviews.
export type ReviewPage = {
  items: Review[];
  total: number;
  limit: number;
  offset: number;
};

// How many cards to add each time the user asks for more.
export const REVIEWS_PAGE_SIZE = 4;

// How many reviews to ask the server for at a time (the endpoint allows up to 100).
export const REVIEWS_FETCH_SIZE = 50;

const LONG_REVIEW_LENGTH = 280;

// offset is how many reviews are already loaded, so the next call continues from there.
export async function fetchReviews(targetId: string, offset = 0): Promise<ReviewPage> {
  const query = new URLSearchParams({
    limit: String(REVIEWS_FETCH_SIZE),
    offset: String(offset),
  });
  return apiFetch<ReviewPage>(`/api/v1/targets/${targetId}/reviews?${query}`);
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
