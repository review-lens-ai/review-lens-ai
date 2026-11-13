// useTargetSummary: the target's review count and ratings, refreshed when a run saves
// reviews (S2-008). It loads through the generated useGetTargetSummary hook; MSW plays the backend.
import { describe, expect, it, vi } from "vitest";
import type { IngestionRun } from "@/lib/ingestion-runs";
import {
  API,
  apiErrorResponse,
  http,
  ingestionRun,
  jsonResponse,
  queryWrapper,
  renderHook,
  server,
  waitFor,
} from "@/test";

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import { useTargetSummary } from "./use-target-summary";

const TARGET_ID = "11111111-1111-1111-1111-111111111111";
const SUMMARY_URL = `${API}/api/v1/targets/${TARGET_ID}/summary`;

function summaryOf(reviewCount: number) {
  return {
    review_count: reviewCount,
    average_rating: reviewCount === 0 ? null : 4,
    rating_counts: [1, 2, 3, 4, 5].map((rating) => ({
      rating,
      count: rating === 4 ? reviewCount : 0,
    })),
    first_reviewed_at: reviewCount === 0 ? null : "2024-03-02T00:00:00Z",
    last_reviewed_at: reviewCount === 0 ? null : "2026-11-08T00:00:00Z",
  };
}

// The backend answers with a summary for `reviewCount` reviews. Returns a mock, so a test
// can count how many times the summary was asked for.
function backendHas(reviewCount: number) {
  const asked = vi.fn();
  server.use(
    http.get(SUMMARY_URL, () => {
      asked();
      return jsonResponse(summaryOf(reviewCount));
    }),
  );
  return asked;
}

type LatestRun = IngestionRun | null | undefined;

function renderSummary(latestRun: LatestRun, wrapper = queryWrapper()) {
  return renderHook(
    ({ run }: { run: LatestRun }) => useTargetSummary(TARGET_ID, { latestRun: run }),
    {
      wrapper,
      initialProps: { run: latestRun },
    },
  );
}

const runningRun = ingestionRun({ id: "run-2", status: "RUNNING", finished_at: null });
const finishedRun = ingestionRun({ id: "run-2", status: "COMPLETED" });

describe("useTargetSummary", () => {
  it("waits for the latest run, then loads the summary once", async () => {
    const asked = backendHas(5);
    const { result, rerender } = renderSummary(undefined);

    // The latest run is still loading, so nothing is asked yet.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(asked).not.toHaveBeenCalled();
    expect(result.current.summary).toBeNull();

    rerender({ run: finishedRun });

    await waitFor(() => expect(result.current.summary?.review_count).toBe(5));
    expect(asked).toHaveBeenCalledOnce();
  });

  it("loads the empty summary for a target that was never ingested", async () => {
    backendHas(0);

    const { result } = renderSummary(null);

    await waitFor(() => expect(result.current.summary?.review_count).toBe(0));
    expect(result.current.summary?.average_rating).toBeNull();
  });

  it("asks again when the run finishes, so the new reviews show up", async () => {
    backendHas(5);
    const { result, rerender } = renderSummary(runningRun);
    await waitFor(() => expect(result.current.summary?.review_count).toBe(5));

    backendHas(8);
    rerender({ run: finishedRun });

    await waitFor(() => expect(result.current.summary?.review_count).toBe(8));
  });

  it("reuses the loaded summary when the page is opened again for the same run", async () => {
    const asked = backendHas(5);
    const wrapper = queryWrapper();
    const first = renderSummary(finishedRun, wrapper);
    await waitFor(() => expect(first.result.current.summary?.review_count).toBe(5));
    first.unmount();

    const second = renderSummary({ ...finishedRun }, wrapper);

    // The summary is there at once, and the backend was asked only the first time.
    expect(second.result.current.summary?.review_count).toBe(5);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(asked).toHaveBeenCalledOnce();
  });

  it("starts fresh for a different run of the same target", async () => {
    backendHas(5);
    const { result, rerender } = renderSummary(finishedRun);
    await waitFor(() => expect(result.current.summary?.review_count).toBe(5));

    backendHas(9);
    rerender({ run: ingestionRun({ id: "run-3", status: "COMPLETED" }) });

    await waitFor(() => expect(result.current.summary?.review_count).toBe(9));
  });

  it("reports a failure when the summary can't load", async () => {
    server.use(
      http.get(SUMMARY_URL, () => apiErrorResponse(404, "NOT_FOUND", "Target not found.")),
    );

    const { result } = renderSummary(finishedRun);

    await waitFor(() => expect(result.current.failed).toBe(true));
    expect(result.current.summary).toBeNull();
  });
});
