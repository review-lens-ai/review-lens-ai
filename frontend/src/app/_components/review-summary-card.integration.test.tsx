// ReviewSummaryCard (S2-006): the review count and rating summary for the active target.
// The card is rendered on its own with the real useTargetSummary hook; MSW plays the backend.
import { describe, expect, it, vi } from "vitest";
import type { IngestionRun } from "@/lib/ingestion-runs";
import {
  API,
  apiErrorResponse,
  http,
  ingestionRun,
  jsonResponse,
  queryWrapper,
  render,
  screen,
  server,
  within,
} from "@/test";

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import { ReviewSummaryCard } from "./review-summary-card";

const TARGET_ID = "11111111-1111-1111-1111-111111111111";
const SUMMARY_URL = `${API}/api/v1/targets/${TARGET_ID}/summary`;

// What the backend returns for five saved reviews rated 5, 5, 4, 2 and 1:
// 5 reviews, average (5 + 5 + 4 + 2 + 1) / 5 = 3.4, and two 5s, one 4, no 3, one 2, one 1.
// The backend lists the ratings lowest first, so the card has to sort them for display.
const FIVE_REVIEWS = {
  review_count: 5,
  average_rating: 3.4,
  rating_counts: [
    { rating: 1, count: 1 },
    { rating: 2, count: 1 },
    { rating: 3, count: 0 },
    { rating: 4, count: 1 },
    { rating: 5, count: 2 },
  ],
};

const NO_REVIEWS = {
  review_count: 0,
  average_rating: null,
  rating_counts: [1, 2, 3, 4, 5].map((rating) => ({ rating, count: 0 })),
};

// The backend answers every summary request with `summary`. Returns a mock, so a test can
// count how many times the summary was asked for.
function backendReturns(summary: object) {
  const asked = vi.fn();
  server.use(
    http.get(SUMMARY_URL, () => {
      asked();
      return jsonResponse(summary);
    }),
  );
  return asked;
}

const finishedRun = ingestionRun({ id: "run-1", target_id: TARGET_ID, status: "COMPLETED" });

type LatestRun = IngestionRun | null | undefined;

function card(latestRun: LatestRun) {
  return (
    <ReviewSummaryCard
      targetId={TARGET_ID}
      targetName="Joe's Pizza"
      platform="Google Maps"
      latestRun={latestRun}
    />
  );
}

// No default value: renderCard(undefined) must mean "the latest run is still loading".
function renderCard(latestRun: LatestRun) {
  return render(card(latestRun), { wrapper: queryWrapper() });
}

// Each rating row as [rating, count], top to bottom, e.g. ["5 stars", "2"].
function ratingRows(): [string, string][] {
  const table = screen.getByRole("table", { name: "Reviews by rating" });
  const [, ...rows] = within(table).getAllByRole("row");
  return rows.map((row) => [
    within(row).getByRole("rowheader").textContent ?? "",
    within(row).getAllByRole("cell").at(-1)?.textContent ?? "",
  ]);
}

describe("ReviewSummaryCard", () => {
  // S2-BR-003, S2-BR-004: exact numbers for a known set of reviews.
  it("shows the review count, average and count for each rating", async () => {
    backendReturns(FIVE_REVIEWS);

    renderCard(finishedRun);

    expect(await screen.findByText("5 reviews")).toBeTruthy();
    expect(screen.getByText("3.4 out of 5")).toBeTruthy();
    expect(ratingRows()).toEqual([
      ["5 stars", "2"],
      ["4 stars", "1"],
      ["3 stars", "0"],
      ["2 stars", "1"],
      ["1 star", "1"],
    ]);
  });

  // S2-BR-001: the card itself says which target the numbers belong to.
  it("names the target and platform the summary is for", async () => {
    backendReturns(FIVE_REVIEWS);

    renderCard(finishedRun);

    expect(
      screen.getByRole("region", { name: "Review summary for Joe's Pizza · Google Maps" }),
    ).toBeTruthy();
    await screen.findByText("5 reviews");
  });

  it("shows the average with one decimal, as Google Maps does", async () => {
    // Ratings 5, 4 and 4: the backend rounds the average to 4.33.
    backendReturns({
      review_count: 3,
      average_rating: 4.33,
      rating_counts: [1, 2, 3, 4, 5].map((rating) => ({
        rating,
        count: { 4: 2, 5: 1 }[rating] ?? 0,
      })),
    });

    renderCard(finishedRun);

    expect(await screen.findByText("4.3 out of 5")).toBeTruthy();
  });

  it("shows a loading placeholder, and asks nothing, while the latest run is loading", async () => {
    const asked = backendReturns(FIVE_REVIEWS);

    renderCard(undefined);

    expect(screen.getByRole("status", { name: "Loading summary" })).toBeTruthy();
    // The target is still named while the numbers load.
    expect(screen.getByRole("heading", { name: /Joe's Pizza/ })).toBeTruthy();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(asked).not.toHaveBeenCalled();
  });

  it("shows the empty state, and no average or counts, when there are no reviews", async () => {
    backendReturns(NO_REVIEWS);

    renderCard(null);

    expect(await screen.findByText(/No reviews yet/)).toBeTruthy();
    expect(screen.queryByText(/out of 5/)).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  // The backend answers 404 for another user's target, so user B sees an error, never
  // user A's numbers. (The backend's own two-user test is in PR #114.)
  it.each([
    [404, "NOT_FOUND", "Target not found."],
    [500, "INTERNAL_ERROR", "Something went wrong."],
  ])("shows an error, and no numbers, when the summary answers %i", async (status, code, msg) => {
    server.use(http.get(SUMMARY_URL, () => apiErrorResponse(status, code, msg)));

    renderCard(finishedRun);

    expect(await screen.findByText("We couldn't load the summary.")).toBeTruthy();
    expect(screen.queryByText(/out of 5/)).toBeNull();
  });
});
