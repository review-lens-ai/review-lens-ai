"use client";

// TargetNameEditor (S3-002): the workspace H1 with a Rename button that edits the name
// in place. Only the name is sent; the backend checks it (the same rules as creating a
// target) and is the only place those rules live. After a save, the header and the
// sidebar both show the new name. ui-ux-standards §5 (buttons), §6.2 (inline errors).
import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { PencilIcon, TriangleAlertIcon } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/app/_components/ui/button";
import { Input } from "@/app/_components/ui/input";
import { ApiError, errorMessage, isUnauthorized } from "@/lib/api";
import { getGetTargetQueryKey, useRenameTarget } from "@/lib/generated/api";
import { useTargets } from "@/lib/hooks/use-targets";
import type { Target } from "@/lib/targets";

// Why the last save failed. A 422 names the field; anything else is one message.
// A 401 redirects to sign in (QueryProvider), so it shows nothing here.
function saveError(error: Error | null): string | null {
  if (!error || isUnauthorized(error)) return null;
  if (error instanceof ApiError && error.code === "VALIDATION_ERROR") {
    return error.details.find((issue) => issue.field === "name")?.message ?? error.message;
  }
  return errorMessage(error);
}

export function TargetNameEditor({ target }: { target: Target }) {
  const queryClient = useQueryClient();
  const { refresh: refreshSidebar } = useTargets();
  const [editing, setEditing] = useState(false);
  // What the user is typing: UI state, not server data.
  const [draft, setDraft] = useState(target.name);
  const input = useRef<HTMLInputElement>(null);
  const renameButton = useRef<HTMLButtonElement>(null);

  const rename = useRenameTarget({
    mutation: {
      // Stays "Saving…" until the sidebar has the new name too.
      onSuccess: async (renamed) => {
        queryClient.setQueryData(getGetTargetQueryKey(target.id), renamed);
        await refreshSidebar();
        toast.success("Target renamed.");
        setEditing(false);
      },
    },
  });
  const saving = rename.isPending;
  // The same value until the next failure, so focus moves once per failure.
  const error = useMemo(() => saveError(rename.error), [rename.error]);

  // §6.2: on opening, and after a failed save, keyboard focus goes to the field.
  useEffect(() => {
    if (editing) input.current?.focus();
  }, [editing, error]);

  // Every edit starts from the saved name, so Cancel doesn't need to put it back.
  function startEditing() {
    rename.reset();
    setDraft(target.name);
    setEditing(true);
  }

  function cancel() {
    rename.reset();
    setEditing(false);
    // Put focus back where the user started, so the keyboard isn't lost.
    requestAnimationFrame(() => renameButton.current?.focus());
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    rename.mutate({ targetId: target.id, data: { name: draft } });
  }

  if (!editing) {
    return (
      <div className="flex min-w-0 items-center gap-1">
        <h1 className="min-w-0 truncate text-3xl font-semibold tracking-tight">{target.name}</h1>
        <Button
          ref={renameButton}
          type="button"
          variant="ghost"
          size="icon-sm"
          aria-label="Rename target"
          title="Rename target"
          onClick={startEditing}
        >
          <PencilIcon aria-hidden="true" />
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-1">
      {/* The page keeps its H1 while the name is being edited. */}
      <h1 className="sr-only">{target.name}</h1>
      <form onSubmit={onSubmit} noValidate className="flex flex-wrap items-center gap-2">
        <label htmlFor="target-name-edit" className="sr-only">
          Target name
        </label>
        <Input
          ref={input}
          id="target-name-edit"
          type="text"
          autoComplete="off"
          value={draft}
          disabled={saving}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Escape") cancel();
          }}
          aria-invalid={Boolean(error)}
          aria-describedby={error ? "target-name-edit-error" : undefined}
          className="h-10 w-72 max-w-full text-lg"
        />
        <Button type="button" variant="outline" onClick={cancel} disabled={saving}>
          Cancel
        </Button>
        <Button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </form>
      {error && (
        <p
          id="target-name-edit-error"
          role="alert"
          className="flex items-center gap-1 text-sm text-destructive"
        >
          <TriangleAlertIcon className="size-4 shrink-0" aria-hidden="true" />
          {error}
        </p>
      )}
    </div>
  );
}
