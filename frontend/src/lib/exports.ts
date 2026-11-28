// S3-017, S3-018, S3-019: saving a target's review data (CSV) or analysis (Markdown).
// The backend builds the file and suggests a name from the analysis; this fetches it, lets
// the user change the name, and hands it to the browser to save.
import { apiFetchFile } from "@/lib/api";

// The same two words as ExportKind in the backend (backend/app/services/export.py):
// the last part of the URL and of the file name.
export type ExportKind = "reviews" | "analysis";

export const EXPORT_LABELS: Record<ExportKind, string> = {
  reviews: "Reviews (CSV)",
  analysis: "Analysis (Markdown)",
};

// The same extensions as EXPORT_EXTENSIONS in the backend.
const EXPORT_EXTENSIONS: Record<ExportKind, string> = { reviews: "csv", analysis: "md" };

// Used only if the backend sends no file name.
const FALLBACK_FILENAMES: Record<ExportKind, string> = {
  reviews: "reviews.csv",
  analysis: "analysis.md",
};

// The file and the name the backend suggests for it.
export async function fetchExport(
  targetId: string,
  kind: ExportKind,
): Promise<{ blob: Blob; filename: string }> {
  const { blob, filename } = await apiFetchFile(`/api/v1/targets/${targetId}/export/${kind}`);
  return { blob, filename: filename ?? FALLBACK_FILENAMES[kind] };
}

// The name a user typed, made safe to save: no folder or reserved characters, never empty,
// and always ending in the right extension so the file opens in the right program.
export function cleanFilename(typed: string, kind: ExportKind): string {
  const extension = `.${EXPORT_EXTENSIONS[kind]}`;
  const name = typed.replace(/[\/:*?"<>|]/g, "-").trim();
  const base = name.toLowerCase().endsWith(extension) ? name.slice(0, -extension.length) : name;
  return base.trim() ? `${base.trim()}${extension}` : FALLBACK_FILENAMES[kind];
}

export function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
