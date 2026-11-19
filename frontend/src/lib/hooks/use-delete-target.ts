// S3-005: delete one of the user's targets. The backend removes its ingestion runs and
// reviews with it (cascade delete, docs/architecture/target-deletion.md).
//
// The request is the generated useDeleteTarget mutation, so a 401 is handled for every
// request by the QueryProvider. On success the sidebar's target list is reloaded before
// onDeleted runs, so the deleted target is gone from it by the time the page changes.
import { useCallback } from "react";
import { errorMessage, isNotFound, isUnauthorized } from "@/lib/api";
import { useDeleteTarget as useDeleteTargetRequest } from "@/lib/generated/api";
import { useTargets } from "@/lib/hooks/use-targets";

export function useDeleteTarget(targetId: string, { onDeleted }: { onDeleted: () => void }) {
  const { refresh } = useTargets();
  const { mutate, isPending, error, reset } = useDeleteTargetRequest();

  const deleteTarget = useCallback(() => {
    const finish = async () => {
      await refresh();
      onDeleted();
    };
    mutate(
      { targetId },
      {
        onSuccess: finish,
        // Not found: it was already deleted (e.g. in another tab), so the result the user
        // asked for is already true. Another user's target is also a 404, but this page
        // only opens the user's own.
        onError: (failure) => {
          if (isNotFound(failure)) void finish();
        },
      },
    );
  }, [mutate, targetId, refresh, onDeleted]);

  // A 401 is redirecting to /login, and a 404 counts as deleted: neither needs a message.
  const message =
    error && !isUnauthorized(error) && !isNotFound(error) ? errorMessage(error) : null;

  return { deleteTarget, deleting: isPending, error: message, reset };
}
