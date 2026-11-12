// A target's summary (review count, average rating, rating counts), refreshed when a run
// saves reviews. Built on the generated hook for GET /api/v1/targets/{id}/summary (S2-008).
import { keepPreviousData } from "@tanstack/react-query";
import { getGetTargetSummaryQueryKey, useGetTargetSummary } from "@/lib/generated/api";
import { runKeyOf } from "@/lib/hooks/use-reviews";
import type { IngestionRun } from "@/lib/ingestion-runs";

// latestRun: the target's newest run, null if it has none, or undefined while it loads.
export function useTargetSummary(
  targetId: string,
  { latestRun }: { latestRun: IngestionRun | null | undefined },
) {
  const runKey = runKeyOf(latestRun);

  const summary = useGetTargetSummary(targetId, {
    query: {
      // The summary only changes when a run saves reviews, so the run is part of the key:
      // a new or finished run means a fresh request, and the same run reuses what loaded.
      queryKey: [...getGetTargetSummaryQueryKey(targetId), runKey],
      staleTime: Infinity,
      // Wait for the latest run, so the first load isn't repeated once it arrives.
      enabled: runKey !== undefined,
      // While the numbers for a new run load, keep showing the old ones.
      placeholderData: keepPreviousData,
    },
  });

  return {
    // null until the first answer has arrived.
    summary: summary.data ?? null,
    // The summary couldn't load (e.g. a 404 for a target that isn't the user's).
    failed: summary.isError,
  };
}
