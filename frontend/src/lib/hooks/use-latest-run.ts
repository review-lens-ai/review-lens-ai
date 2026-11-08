// S1-019, S1-023: a target's newest ingestion run, kept fresh while it is active, and
// starting a new one. Built on the generated hooks for the two ingestion-run endpoints.
import { useCallback, useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import {
  getGetLatestIngestionRunQueryKey,
  useGetLatestIngestionRun,
  useStartIngestionRun,
} from "@/lib/generated/api";
import { useTargets } from "@/lib/hooks/use-targets";
import { isActive, type IngestionRun } from "@/lib/ingestion-runs";

// While a run is queued or running, check for updates this often...
const POLL_EVERY_MS = 3_000;
// ...but stop once it has been going this long: it is stuck. (The backend fails a stuck
// run as TIMEOUT when the next run for the target is started.)
const POLL_LIMIT_MS = 5 * 60_000;

// How often to ask for the run again, or false to stop asking.
function pollInterval(run: IngestionRun | null | undefined): number | false {
  if (!run || !isActive(run.status)) return false;
  const runningFor = Date.now() - Date.parse(run.started_at ?? run.queued_at);
  return runningFor < POLL_LIMIT_MS ? POLL_EVERY_MS : false;
}

export function useLatestRun(targetId: string) {
  const queryClient = useQueryClient();
  const { refresh: refreshTargets } = useTargets();
  const latestKey = getGetLatestIngestionRunQueryKey(targetId);

  const latest = useGetLatestIngestionRun(targetId, {
    // TanStack works this out again after every update, so polling starts when a run
    // becomes active and stops when it finishes.
    query: { refetchInterval: (query) => pollInterval(query.state.data?.run) },
  });

  const start = useStartIngestionRun({
    mutation: {
      // Show the QUEUED run straight away; polling takes over from there.
      onSuccess: (run) => queryClient.setQueryData(latestKey, { run }),
      // 409: a run is already active. Load it, so the page shows it instead.
      onError: (error) => {
        if (error instanceof ApiError && error.status === 409) {
          void queryClient.invalidateQueries({ queryKey: latestKey });
        }
      },
    },
  });

  // The same function every render, in case a caller lists it in effect dependencies.
  const { mutate } = start;
  const startRun = useCallback(() => mutate({ targetId }), [mutate, targetId]);

  // null when the target was never ingested (or the run couldn't load).
  const run = latest.data?.run ?? null;

  // The sidebar shows this target's status too, so reload it whenever the status changes.
  const status = run?.status ?? null;
  useEffect(() => {
    if (status) void refreshTargets();
  }, [status, refreshTargets]);

  return {
    run,
    // Whether the first answer (a run, no run, or an error) has arrived.
    loaded: !latest.isPending,
    start: startRun,
    starting: start.isPending,
    // Why the last start was refused, or null.
    startError: start.error,
  };
}
