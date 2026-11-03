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
