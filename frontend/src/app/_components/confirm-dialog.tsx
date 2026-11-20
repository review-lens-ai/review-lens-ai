"use client";

// ConfirmDialog (ui-ux-standards.md §5, §9): asks before a destructive action. Focus is
// trapped inside; Esc, Cancel and the close button all cancel. Cancel sits on the left,
// the danger button on the right.
//
// The confirm button is a plain Button, not AlertDialogAction, so the dialog stays open
// while the action runs and can show why it failed. The caller closes it on success.
import { Loader2Icon, XIcon } from "lucide-react";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/app/_components/ui/alert-dialog";
import { Button } from "@/app/_components/ui/button";

export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  confirmLabel,
  pendingLabel,
  pending,
  error,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  // One sentence saying what happens; screen readers read it with the title.
  description: string;
  // Anything else the dialog shows below the description, e.g. what will be removed.
  children?: React.ReactNode;
  // Verb + noun, e.g. "Delete Analysis"; pendingLabel is shown while it runs ("Deleting…").
  confirmLabel: string;
  pendingLabel: string;
  pending: boolean;
  // Why the last attempt failed, or null.
  error: string | null;
  onConfirm: () => void;
}) {
  // Nothing closes the dialog while the action runs, so it can't be left half-seen.
  const close = (next: boolean) => !pending && onOpenChange(next);

  return (
    <AlertDialog open={open} onOpenChange={close}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <div className="flex items-start justify-between gap-4">
            <AlertDialogTitle>{title}</AlertDialogTitle>
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Close"
              disabled={pending}
              onClick={() => close(false)}
              className="-mt-1 -mr-2 shrink-0 text-muted-foreground"
            >
              <XIcon aria-hidden="true" />
            </Button>
          </div>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        {children}
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <Button variant="destructive" onClick={onConfirm} disabled={pending}>
            {pending && <Loader2Icon className="animate-spin" aria-hidden="true" />}
            {pending ? pendingLabel : confirmLabel}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
