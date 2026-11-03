// Shapes returned by the backend /api/v1/targets endpoints (snake_case, as sent).
export type Target = {
  id: string;
  name: string;
  platform: string;
  source_url: string;
  // Status of the newest ingestion run; null means no ingestion yet.
  ingestion_status: "QUEUED" | "RUNNING" | "COMPLETED" | "PARTIAL" | "FAILED" | null;
  created_at: string;
  updated_at: string;
};

const PLATFORM_LABELS: Record<string, string> = {
  google_maps: "Google Maps",
};

export function platformLabel(platform: string): string {
  return PLATFORM_LABELS[platform] ?? platform;
}

// The platform is fixed by the backend (app/platforms.py); the form only displays it.
export const SUPPORTED_PLATFORM = "google_maps";
export const SUPPORTED_PLATFORM_LABEL = platformLabel(SUPPORTED_PLATFORM);

// The summary above a rejected form (ui-ux-standards.md §6.2).
export function fixFieldsMessage(count: number): string {
  return `Fix ${count} ${count === 1 ? "field" : "fields"} to continue.`;
}

// A short, readable form of the source link for lists, e.g. "www.google.com/maps/place/Joes+Pizza".
export function shortSource(url: string): string {
  try {
    const { host, pathname } = new URL(url);
    const text = `${host}${pathname}`.replace(/\/$/, "");
    return text.length > 60 ? `${text.slice(0, 57)}...` : text;
  } catch {
    return url;
  }
}
