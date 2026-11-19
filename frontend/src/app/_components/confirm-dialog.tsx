"use client";

// ConfirmDialog (ui-ux-standards.md §5, §9): asks before a destructive action. Focus is
// trapped inside and Esc cancels. Cancel sits on the left, the danger button on the right.
//
// The confirm button is a plain Button, not AlertDialogAction, so the dialog stays open
// while the action runs and can show why it failed. The caller closes it on success.
import { Loader2Icon } from "lucide-react";
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
  confirmLabel,
  pendingLabel,
  pending,
  error,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: React.ReactNode;
  description: React.ReactNode;
  // Verb + noun, e.g. "Delete target"; pendingLabel is shown while it runs ("Deleting…").
  confirmLabel: string;
  pendingLabel: string;
  pending: boolean;
  // Why the last attempt failed, or null.
  error: string | null;
  onConfirm: () => void;
}) {
  return (
    <AlertDialog
      open={open}
      // Esc and Cancel do nothing while the action runs, so it can't be left half-seen.
      onOpenChange={(next) => !pending && onOpenChange(next)}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
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
