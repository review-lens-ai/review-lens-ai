// The signed-in user's targets, shared by the sidebar and the pages: TanStack Query
// keeps one cached copy, so a new target appears everywhere after refresh().
import { useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ApiError } from "@/lib/api";
import { getListTargetsQueryKey, useListTargets } from "@/lib/generated/api";
import { isUnauthorized } from "@/lib/query-client";
import type { Target } from "@/lib/targets";

type TargetsState = {
  // null while the first load is in progress.
  targets: Target[] | null;
  error: string | null;
  // Reload the list, e.g. after creating a target or when a run's status changes.
  refresh: () => Promise<void>;
};

export function useTargets(): TargetsState {
  const queryClient = useQueryClient();
  const { data, error } = useListTargets();
  // The same function every render: pages put it in useEffect dependencies.
  const refresh = useCallback(
    () => queryClient.invalidateQueries({ queryKey: getListTargetsQueryKey() }),
    [queryClient],
  );

  return {
    targets: data?.items ?? null,
    // A 401 is handled by the QueryProvider (redirect to /login); no message needed.
    error:
      error && !isUnauthorized(error)
        ? error instanceof ApiError
          ? error.message
          : "Could not reach the server. Please try again."
        : null,
    refresh,
  };
}
