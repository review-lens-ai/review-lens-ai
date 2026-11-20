// S3-017, S3-018, S3-019: saving a target's review data (CSV) or analysis (Markdown).
// The backend builds the file and names it after the analysis; this only fetches it and
// hands it to the browser to save.
import { apiFetchFile } from "@/lib/api";

// The same two words as ExportKind in the backend (backend/app/services/export.py):
// the last part of the URL and of the file name.
export type ExportKind = "reviews" | "analysis";

export const EXPORT_LABELS: Record<ExportKind, string> = {
  reviews: "Export reviews (CSV)",
  analysis: "Export analysis (Markdown)",
};

// Used only if the backend sends no file name.
const FALLBACK_FILENAMES: Record<ExportKind, string> = {
  reviews: "reviews.csv",
  analysis: "analysis.md",
};

export async function downloadExport(targetId: string, kind: ExportKind): Promise<void> {
  const { blob, filename } = await apiFetchFile(`/api/v1/targets/${targetId}/export/${kind}`);
  saveFile(blob, filename ?? FALLBACK_FILENAMES[kind]);
}

function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
