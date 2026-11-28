"use client";

// S3-017, S3-018, S3-019: the header's Export menu. One button opens a list of the two
// files: the target's reviews (CSV) or its analysis (Markdown). The file is fetched first,
// then the user can change its name (the backend suggests one from the analysis name)
// before it is saved.
import { useState } from "react";
import { ChevronDownIcon, DownloadIcon, Loader2Icon } from "lucide-react";
import { Button } from "@/app/_components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/app/_components/ui/dropdown-menu";
import { Input } from "@/app/_components/ui/input";
import { errorMessage } from "@/lib/api";
import {
  cleanFilename,
  EXPORT_LABELS,
  fetchExport,
  saveFile,
  type ExportKind,
} from "@/lib/exports";

const EXPORT_KINDS: ExportKind[] = ["reviews", "analysis"];

// A file that is fetched and waiting for the user to confirm its name.
type ReadyExport = { kind: ExportKind; blob: Blob };

export function ExportMenu({ targetId, hasReviews }: { targetId: string; hasReviews: boolean }) {
  // True while a file is being fetched, so the button shows progress and the menu waits.
  const [exporting, setExporting] = useState(false);
  const [ready, setReady] = useState<ReadyExport | null>(null);
  const [filename, setFilename] = useState("");
  const [failure, setFailure] = useState<string | null>(null);

  async function onChoose(kind: ExportKind) {
    setExporting(true);
    setReady(null);
    setFailure(null);
    try {
      const file = await fetchExport(targetId, kind);
      setFilename(file.filename);
      setReady({ kind, blob: file.blob });
    } catch (error) {
      setFailure(errorMessage(error));
    } finally {
      setExporting(false);
    }
  }

  function onSave() {
    if (!ready) return;
    saveFile(ready.blob, cleanFilename(filename, ready.kind));
    setReady(null);
  }

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" disabled={exporting}>
            {exporting ? (
              <Loader2Icon className="animate-spin" aria-hidden="true" />
            ) : (
              <DownloadIcon aria-hidden="true" />
            )}
            Export
            <ChevronDownIcon aria-hidden="true" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          {EXPORT_KINDS.map((kind) => (
            <DropdownMenuItem
              key={kind}
              onSelect={() => onChoose(kind)}
              // A CSV of nothing is not useful; the Markdown still describes the analysis.
              disabled={kind === "reviews" && !hasReviews}
            >
              {EXPORT_LABELS[kind]}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      {ready && (
        <form
          className="flex w-full flex-wrap items-center justify-end gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            onSave();
          }}
        >
          <label htmlFor="export-filename" className="text-sm text-muted-foreground">
            Save as
          </label>
          <Input
            id="export-filename"
            value={filename}
            onChange={(event) => setFilename(event.target.value)}
            className="w-72"
            autoFocus
          />
          <Button type="submit">Save</Button>
          <Button type="button" variant="ghost" onClick={() => setReady(null)}>
            Cancel
          </Button>
        </form>
      )}
      {failure && (
        <p role="alert" className="w-full text-sm text-destructive">
          {failure}
        </p>
      )}
    </>
  );
}
