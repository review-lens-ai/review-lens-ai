// One page of a target's saved reviews, with Next/Previous and (S3-016) rating and date
// filters. Built on the generated hook for GET /api/v1/targets/{id}/reviews.
import { useState } from "react";
import { keepPreviousData, useQueryClient } from "@tanstack/react-query";
import {
  getListTargetReviewsQueryKey,
  getListTargetReviewsQueryOptions,
  useListTargetReviews,
} from "@/lib/generated/api";
import type { IngestionRun } from "@/lib/ingestion-runs";
import { hasFilters, NO_FILTERS, type ReviewFilters } from "@/lib/review-filters";
import { REVIEWS_PAGE_SIZE, reviewPageParams } from "@/lib/reviews";

// Which run the reviews belong to: that run, in one of its two states (still going, or
// finished at a given time). null when the target has no run; undefined until the latest
// run has loaded. The run's id is part of it: two runs that are both still going must not
// share cached pages, or a new run would show the list from before the previous one.
export type RunKey = string | null | undefined;

// Shared with useTargetSummary: both change only when a run saves reviews.
export function runKeyOf(run: IngestionRun | null | undefined): RunKey {
  if (run === undefined || run === null) return run;
  return `${run.id}:${run.finished_at ?? "going"}`;
}

// around: the review a link asked for. Only the first load uses it; Next/Previous drop it.
// filters: the applied filters (S3-016). They stay when a run finishes, until cleared.
type PageView = {
  page: number;
  failed: boolean;
  forRun: RunKey;
  around?: string;
  filters: ReviewFilters;
};

// Saved reviews only change when a run saves them. The run is part of each page's cache
// key, so a new run means fresh pages, and a page already loaded for this run is reused.
// The filters are part of the params, so each filter has its own cached pages.
function pageOptions(
  targetId: string,
  page: number,
  runKey: RunKey,
  around?: string,
  filters: ReviewFilters = NO_FILTERS,
) {
  const params = reviewPageParams(page, around, filters);
  return {
    queryKey: [...getListTargetReviewsQueryKey(targetId, params), runKey],
    staleTime: Infinity,
  };
}

// latestRun: the target's newest run, null if it has none, or undefined while loading.
// focusReviewId: open on the page that holds this review (a link to one review).
export function useReviews(
  targetId: string,
  {
    enabled,
    latestRun,
    focusReviewId,
  }: {
    enabled: boolean;
    latestRun: IngestionRun | null | undefined;
    focusReviewId?: string | null;
  },
) {
  const runKey = runKeyOf(latestRun);
  const queryClient = useQueryClient();
  const [view, setView] = useState<PageView>({
    page: 0,
    failed: false,
    forRun: runKey,
    around: focusReviewId ?? undefined,
    filters: NO_FILTERS,
  });
  const [pageLoading, setPageLoading] = useState(false);

  // The page on screen belongs to one run. When a run starts or finishes, new reviews
  // may have been saved, so the list starts again from page 1 (#68), with the same
  // filters. The first load (the latest run was still unknown) keeps the linked review;
  // a later run starts the list again and drops it.
  const current: PageView =
    view.forRun === runKey
      ? view
      : {
          page: 0,
          failed: false,
          forRun: runKey,
          around: view.forRun === undefined ? view.around : undefined,
          filters: view.filters,
        };
  const filtered = hasFilters(current.filters);
  const ready = enabled && runKey !== undefined;

  const shown = useListTargetReviews(
    targetId,
    reviewPageParams(current.page, current.around, current.filters),
    {
      query: {
        ...pageOptions(targetId, current.page, runKey, current.around, current.filters),
        // Wait for the latest run, so the first load isn't repeated once it arrives.
        enabled: ready,
        // While a new run's page 1 loads, keep showing the reviews from before (§7.3).
        placeholderData: keepPreviousData,
      },
    },
  );

  // How many reviews the target has in all. The page uses it outside the list (header,
  // banners, Q&A), where a filter must not change it. Unfiltered, it is the list's own
  // total; filtered, it comes from the unfiltered first page, which is usually cached.
  const everything = useListTargetReviews(targetId, reviewPageParams(0), {
    query: { ...pageOptions(targetId, 0, runKey), enabled: ready && filtered },
  });

  // A link to a review that is gone (a newer run replaced it) opens page 1 instead.
  if (shown.isError && current.around) {
    setView({ page: 0, failed: false, forRun: runKey, filters: current.filters });
  }

  // Loads a page first and switches only once it has arrived, so a failure keeps the
  // reviews on screen and says so. Used by Next/Previous and by applying filters.
  async function load(page: number, filters: ReviewFilters): Promise<boolean> {
    const forRun = runKey;
    setPageLoading(true);
    try {
      await queryClient.query(
        getListTargetReviewsQueryOptions(targetId, reviewPageParams(page, undefined, filters), {
          query: pageOptions(targetId, page, forRun, undefined, filters),
        }),
      );
      setView({ page, failed: false, forRun, filters });
      return true;
    } catch {
      // A failed page turn says so under the list; a failed filter change is reported
      // by the filter form, which stays open with the user's choices.
      if (filters === current.filters) setView({ ...current, failed: true, forRun });
      return false;
    } finally {
      setPageLoading(false);
    }
  }

  const data = shown.data;
  const total = data?.total ?? 0;
  return {
    // null until the first page has loaded.
    reviews: data?.items ?? null,
    // The reviews that match the filters (every review when none are applied).
    total,
    // Every saved review of the target, whatever the filters.
    datasetTotal: filtered ? (everything.data?.total ?? total) : total,
    // The page of the reviews on screen (from the answer, so the two always match).
    page: data ? data.offset / REVIEWS_PAGE_SIZE : current.page,
    // The reviews couldn't load at all.
    failed: shown.isError,
    pageLoading,
    // A Next/Previous failed; the page on screen is unchanged.
    pageFailed: current.failed,
    showPage: (page: number) => load(page, current.filters),
    // The applied filters, and applying new ones (NO_FILTERS clears them). Resolves to
    // false when the matching reviews couldn't load; the old ones stay on screen.
    filters: current.filters,
    applyFilters: (filters: ReviewFilters) => load(0, filters),
  };
}
