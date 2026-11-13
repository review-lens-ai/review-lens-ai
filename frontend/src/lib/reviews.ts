// A target's saved reviews. Reviewer names are never stored or sent.
// The types are generated from the backend (npm run api:generate), so they can't drift.
import type {
  ListTargetReviewsParams,
  ReviewPage as GeneratedReviewPage,
  ReviewResponse,
} from "@/lib/generated/api";

export type Review = ReviewResponse;
// One page of a target's reviews. The endpoint is paginated (coding-standards.md §5):
// items is one page, not everything, and total counts all of the target's reviews.
export type ReviewPage = GeneratedReviewPage;

// Reviews per page in the workspace; the server sends one page at a time
// (the endpoint allows up to 100).
export const REVIEWS_PAGE_SIZE = 12;

const LONG_REVIEW_LENGTH = 280;

// page is 0 for the first page; the server skips page * REVIEWS_PAGE_SIZE reviews.
export function reviewPageParams(page: number): ListTargetReviewsParams {
  return { limit: REVIEWS_PAGE_SIZE, offset: page * REVIEWS_PAGE_SIZE };
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
