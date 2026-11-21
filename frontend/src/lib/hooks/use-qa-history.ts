// S3-007: a target's saved questions and answers. S3-008: clearing them.
//
// The requests are hand-written for now (see src/lib/qa-history.ts); a 401 is still
// handled for every request by the QueryProvider.
import { useCallback } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, errorMessage, isUnauthorized } from "@/lib/api";
import {
  clearQaHistory,
  getQaHistory,
  qaHistoryQueryKey,
  type SavedQaExchange,
} from "@/lib/qa-history";

// The target is gone or not this user's. A 404 without this code (e.g. the endpoint not
// deployed yet) only means the history couldn't load.
function isTargetMissing(error: unknown): boolean {
  return error instanceof ApiError && error.code === "NOT_FOUND";
}

type QaHistoryState = {
  // null while loading, and when loading failed.
  exchanges: SavedQaExchange[] | null;
  // The history couldn't load. Q&A still works; earlier questions just aren't shown.
  failed: boolean;
  targetMissing: boolean;
};

// enabled: false until the Q&A panel is first opened, so a workspace visit that never
// opens it sends no request.
export function useQaHistory(targetId: string, { enabled }: { enabled: boolean }): QaHistoryState {
  const { data, error } = useQuery({
    queryKey: qaHistoryQueryKey(targetId),
    queryFn: () => getQaHistory(targetId),
    enabled,
  });

  return {
    exchanges: data?.items ?? null,
    failed: Boolean(error) && !isUnauthorized(error) && !isTargetMissing(error),
    targetMissing: isTargetMissing(error),
  };
}

export function useClearQaHistory(
  targetId: string,
  { onCleared, onTargetMissing }: { onCleared: () => void; onTargetMissing: () => void },
) {
  const queryClient = useQueryClient();
  const { mutate, isPending, error, reset } = useMutation({
    mutationFn: () => clearQaHistory(targetId),
    onSuccess: () => {
      // Coming back to this target later shows the empty history without a reload.
      queryClient.setQueryData(qaHistoryQueryKey(targetId), { items: [] });
      onCleared();
    },
    onError: (failure) => {
      if (isTargetMissing(failure)) onTargetMissing();
    },
  });

  const clear = useCallback(() => mutate(), [mutate]);

  // A 401 is redirecting to /login and a missing target shows the not-found page.
  const message =
    error && !isUnauthorized(error) && !isTargetMissing(error) ? errorMessage(error) : null;

  return { clear, clearing: isPending, error: message, reset };
}
