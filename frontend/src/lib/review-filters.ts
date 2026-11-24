// S3-016: rating and date filters for a target's reviews list.
// The backend does the filtering (GET /targets/{id}/reviews with min_rating, max_rating,
// start_date, end_date and timezone); these helpers turn the user's choices into those
// query parameters, check them before they are sent, and describe them on screen.
import type { ListTargetReviewsParams } from "@/lib/generated/api";

// What the user chose. Every part is optional; they combine with AND.
// Ratings are 1 to 5, inclusive. Dates are "YYYY-MM-DD" (an <input type="date"> value),
// inclusive, in the user's own timezone.
export type ReviewFilters = {
  minRating?: number;
  maxRating?: number;
  startDate?: string;
  endDate?: string;
};

export const NO_FILTERS: ReviewFilters = {};

export function hasFilters(filters: ReviewFilters): boolean {
  return Object.values(filters).some((value) => value !== undefined);
}

// The query parameters for the reviews endpoint. The browser's timezone goes with any
// date, so "Feb 28" means Feb 28 where the user is, the same day the review cards show.
export function filterParams(
  filters: ReviewFilters,
  timezone: string = browserTimezone(),
): Partial<ListTargetReviewsParams> {
  const params: Partial<ListTargetReviewsParams> = {};
  if (filters.minRating !== undefined) params.min_rating = filters.minRating;
  if (filters.maxRating !== undefined) params.max_rating = filters.maxRating;
  if (filters.startDate) params.start_date = filters.startDate;
  if (filters.endDate) params.end_date = filters.endDate;
  if (filters.startDate || filters.endDate) params.timezone = timezone;
  return params;
}

export function browserTimezone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

// Why these filters can't be applied, or null when they can. The backend rejects the
// same cases with a 422; checking here lets the form say so before sending anything.
export function filterProblem(filters: ReviewFilters): string | null {
  const { minRating, maxRating, startDate, endDate } = filters;
  if (minRating !== undefined && maxRating !== undefined && minRating > maxRating) {
    return "Maximum rating can't be lower than minimum.";
  }
  // "YYYY-MM-DD" strings compare in date order.
  if (startDate && endDate && startDate > endDate) {
    return "End date can't be before start date.";
  }
  return null;
}

// e.g. "1–2 stars · Jan 1, 2026 – Mar 1, 2026". Empty when nothing is set.
export function describeFilters(filters: ReviewFilters): string {
  return [ratingText(filters), dateText(filters)].filter(Boolean).join(" · ");
}

function ratingText({ minRating, maxRating }: ReviewFilters): string {
  if (minRating !== undefined && maxRating !== undefined) {
    return minRating === maxRating ? stars(minRating) : `${minRating}–${maxRating} stars`;
  }
  if (minRating !== undefined) return `${stars(minRating)} or more`;
  if (maxRating !== undefined) return `${stars(maxRating)} or fewer`;
  return "";
}

function dateText({ startDate, endDate }: ReviewFilters): string {
  if (startDate && endDate) return `${formatDay(startDate)} – ${formatDay(endDate)}`;
  if (startDate) return `From ${formatDay(startDate)}`;
  if (endDate) return `Until ${formatDay(endDate)}`;
  return "";
}

function stars(n: number): string {
  return `${n} ${n === 1 ? "star" : "stars"}`;
}

// "2026-03-01" -> "Mar 1, 2026". Built from the parts, not new Date("2026-03-01"),
// which reads it as UTC midnight and can show the previous day west of UTC.
export function formatDay(day: string): string {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
