// S1-022: one page of a target's saved reviews, with Next/Previous. Built on the
// generated hook for GET /api/v1/targets/{id}/reviews.
import { useState } from "react";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import {
  getListTargetReviewsQueryKey,
  getListTargetReviewsQueryOptions,
  useListTargetReviews,
} from "@/lib/generated/api";
import type { IngestionRun } from "@/lib/ingestion-runs";
import { REVIEWS_PAGE_SIZE, reviewPageParams } from "@/lib/reviews";

// Which run the reviews belong to: that run, in one of its two states (still going, or
// finished at a given time). null when the target has no run; undefined until the latest
// run has loaded. The run's id is part of it: two runs that are both still going must not
// share cached pages, or a new run would show the list from before the previous one.
type RunKey = string | null | undefined;

function runKeyOf(run: IngestionRun | null | undefined): RunKey {
  if (run === undefined || run === null) return run;
  return `${run.id}:${run.finished_at ?? "going"}`;
}

type PageView = { page: number; failed: boolean; forRun: RunKey };

// Saved reviews only change when a run saves them. The run is part of each page's cache
// key, so a new run means fresh pages, and a page already loaded for this run is reused.
function pageOptions(targetId: string, page: number, runKey: RunKey) {
  const params = reviewPageParams(page);
  return {
    queryKey: [...getListTargetReviewsQueryKey(targetId, params), runKey],
    staleTime: Infinity,
  };
}

// latestRun: the target's newest run, null if it has none, or undefined while loading.
export function useReviews(
  targetId: string,
  { enabled, latestRun }: { enabled: boolean; latestRun: IngestionRun | null | undefined },
) {
  const runKey = runKeyOf(latestRun);
  const queryClient = useQueryClient();
  const [view, setView] = useState<PageView>({ page: 0, failed: false, forRun: runKey });
  const [pageLoading, setPageLoading] = useState(false);

  // The page on screen belongs to one run. When a run starts or finishes, new reviews
  // may have been saved, so the list starts again from page 1 (#68).
  const current: PageView =
    view.forRun === runKey ? view : { page: 0, failed: false, forRun: runKey };

  const shown = useListTargetReviews(targetId, reviewPageParams(current.page), {
    query: {
      ...pageOptions(targetId, current.page, runKey),
      // Wait for the latest run, so the first load isn't repeated once it arrives.
      enabled: enabled && runKey !== undefined,
      // While a new run's page 1 loads, keep showing the reviews from before (§7.3).
      placeholderData: keepPreviousData,
    },
  });

  // Next/Previous: load the page first and switch only once it has arrived, so a failure
  // keeps the current page on screen and says so.
  async function showPage(page: number) {
    const forRun = runKey;
    setPageLoading(true);
    try {
      await queryClient.query(
        getListTargetReviewsQueryOptions(targetId, reviewPageParams(page), {
          query: pageOptions(targetId, page, forRun),
        }),
      );
      setView({ page, failed: false, forRun });
    } catch {
      setView({ page: current.page, failed: true, forRun });
    } finally {
      setPageLoading(false);
    }
  }

  const data = shown.data;
  return {
    // null until the first page has loaded.
    reviews: data?.items ?? null,
    total: data?.total ?? 0,
    // The page of the reviews on screen (from the answer, so the two always match).
    page: data ? data.offset / REVIEWS_PAGE_SIZE : current.page,
    // The reviews couldn't load at all.
    failed: shown.isError,
    pageLoading,
    // A Next/Previous failed; the page on screen is unchanged.
    pageFailed: current.failed,
    showPage,
  };
}
