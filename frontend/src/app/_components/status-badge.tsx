// S1-023: the one shared ingestion status badge (ui-ux-standards.md §7.3 and §8).
// Use this everywhere a status is shown (sidebar, target list, workspace header)
// so the colors, icons and wording never drift apart.
import type { IngestionStatus } from "@/lib/ingestion-runs";

type Badge = { icon: string; label: string; className: string };

const NOT_INGESTED: Badge = {
  icon: "○",
  label: "Not ingested",
  className: "border-gray-200 bg-gray-50 text-gray-700",
};

const BADGES: Record<IngestionStatus, Badge> = {
  QUEUED: {
    icon: "◌",
    label: "Queued",
    className: "border-gray-300 bg-white text-gray-700",
  },
  RUNNING: {
    icon: "◐",
    label: "Ingesting…",
    className: "border-sky-200 bg-sky-50 text-sky-700",
  },
  COMPLETED: {
    icon: "●",
    label: "Ingested",
    className: "border-green-200 bg-green-50 text-green-700",
  },
  PARTIAL: {
    icon: "◑",
    label: "Partially ingested",
    className: "border-amber-200 bg-amber-50 text-amber-700",
  },
  FAILED: {
    icon: "✕",
    label: "Failed",
    className: "border-red-200 bg-red-50 text-red-700",
  },
};

const DOT_CLASSES: Record<IngestionStatus, string> = {
  QUEUED: "bg-muted-foreground",
  RUNNING: "bg-info",
  COMPLETED: "bg-success",
  PARTIAL: "bg-warning",
  FAILED: "bg-destructive",
};

// Unknown or missing statuses fall back to "Not ingested", so the page can
// never crash when the backend adds a new state.
function badgeFor(status: string | null | undefined): Badge {
  return (status && BADGES[status as IngestionStatus]) || NOT_INGESTED;
}

// The same wording as the badge, for places too small for a whole one (the sidebar).
export function statusLabel(status: string | null | undefined): string {
  return badgeFor(status).label;
}

export function statusDotClass(status: string | null | undefined): string {
  return (status && DOT_CLASSES[status as IngestionStatus]) || "bg-gray-400";
}

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const badge = badgeFor(status);
  return (
    // Color is never the only signal: every badge has an icon and a text label.
    <span
      className={`inline-flex items-center gap-1 rounded-full border px-3 py-0.5 text-xs font-medium ${badge.className}`}
    >
      <span aria-hidden="true" className={status === "RUNNING" ? "animate-pulse" : undefined}>
        {badge.icon}
      </span>
      {badge.label}
    </span>
  );
}
