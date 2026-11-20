"use client";

// S3-017, S3-018: the header buttons that save the target's reviews (CSV) or its analysis
// (Markdown). The file name comes from the backend, so it carries the analysis name.
import { useState } from "react";
import { DownloadIcon, Loader2Icon } from "lucide-react";
import { Button } from "@/app/_components/ui/button";
import { errorMessage } from "@/lib/api";
import { downloadExport, EXPORT_LABELS, type ExportKind } from "@/lib/exports";

const EXPORT_KINDS: ExportKind[] = ["reviews", "analysis"];

export function ExportButtons({ targetId, hasReviews }: { targetId: string; hasReviews: boolean }) {
  // The kind being saved right now, so its button shows progress and both wait.
  const [exporting, setExporting] = useState<ExportKind | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  async function onExport(kind: ExportKind) {
    setExporting(kind);
    setFailure(null);
    try {
      await downloadExport(targetId, kind);
    } catch (error) {
      setFailure(errorMessage(error));
    } finally {
      setExporting(null);
    }
  }

  return (
    <>
      {EXPORT_KINDS.map((kind) => (
        <Button
          key={kind}
          variant="outline"
          onClick={() => onExport(kind)}
          // A CSV of nothing is not useful; the Markdown still describes the analysis.
          disabled={exporting !== null || (kind === "reviews" && !hasReviews)}
        >
          {exporting === kind ? (
            <Loader2Icon className="animate-spin" aria-hidden="true" />
          ) : (
            <DownloadIcon aria-hidden="true" />
          )}
          {EXPORT_LABELS[kind]}
        </Button>
      ))}
      {failure && (
        <p role="alert" className="w-full text-sm text-destructive">
          {failure}
        </p>
      )}
    </>
  );
}
