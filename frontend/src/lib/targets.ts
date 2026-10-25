// Shapes returned by the backend /api/v1/targets endpoints (snake_case, as sent).
export type Target = {
  id: string;
  name: string;
  platform: string;
  source_url: string;
  // Status of the newest ingestion run; null means no ingestion yet.
  ingestion_status: "running" | "succeeded" | "partial" | "failed" | null;
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
export const SUPPORTED_PLATFORM_LABEL = platformLabel("google_maps");

// What the user sees for the newest ingestion run's status (null = never ingested).
export function ingestionLabel(status: Target["ingestion_status"]): string {
  switch (status) {
    case "running":
      return "Ingestion in progress";
    case "succeeded":
      return "Ingestion succeeded";
    case "partial":
      return "Ingestion partly succeeded";
    case "failed":
      return "Ingestion failed";
    default:
      return "No ingestion yet";
  }
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
