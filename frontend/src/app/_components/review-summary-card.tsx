"use client";

// ReviewSummaryCard (S2-006): the active target's review count and rating summary.
// Every number comes from the backend's summary of the saved reviews (S2-BR-002), the
// same dataset Q&A reads (S2-BR-007). The card only displays them; it calculates nothing.
import { useId } from "react";
import { CircleAlertIcon, InboxIcon } from "lucide-react";
import { EmptyState } from "@/app/_components/empty-state";
import { Alert, AlertDescription, AlertTitle } from "@/app/_components/ui/alert";
import { Skeleton } from "@/app/_components/ui/skeleton";
import type { ReviewSummary } from "@/lib/generated/api";
import { useTargetSummary } from "@/lib/hooks/use-target-summary";
import { type IngestionRun, reviewCount } from "@/lib/ingestion-runs";

export function ReviewSummaryCard({
  targetId,
  targetName,
  platform,
  latestRun,
}: {
  targetId: string;
  targetName: string;
  // Display text, e.g. "Google Maps".
  platform: string;
  // The target's newest run, null if it has none, or undefined while it loads.
  latestRun: IngestionRun | null | undefined;
}) {
  const { summary, failed } = useTargetSummary(targetId, { latestRun });
  const headingId = useId();

  return (
    <section aria-labelledby={headingId} className="flex max-w-xl flex-col gap-4">
      {/* S2-BR-001: the card names its target, so it is clear even without the page header. */}
      <h2 id={headingId} className="text-xl font-semibold">
        Review summary for {targetName} · {platform}
      </h2>
      <SummaryBody summary={summary} failed={failed} />
    </section>
  );
}

function SummaryBody({ summary, failed }: { summary: ReviewSummary | null; failed: boolean }) {
  // If a refresh fails after numbers have loaded, keep showing those numbers.
  if (failed && !summary) {
    return (
      <Alert variant="destructive">
        <CircleAlertIcon aria-hidden="true" />
        <AlertTitle>We couldn&apos;t load the summary.</AlertTitle>
        <AlertDescription>Reload the page or try again in a few minutes.</AlertDescription>
      </Alert>
    );
  }

  if (!summary) {
    return (
      <div role="status" aria-label="Loading summary" className="flex flex-col gap-4">
        <Skeleton className="h-20 w-full rounded-xl" />
        <Skeleton className="h-40 w-full rounded-xl" />
      </div>
    );
  }

  // No saved reviews: say so, and show no average or counts (never placeholder data).
  if (summary.review_count === 0) {
    return (
      <EmptyState
        icon={<InboxIcon />}
        message="No reviews yet. Use Ingest reviews above to collect them from the source URL."
      />
    );
  }

  // Highest rating first, whatever order the backend sends.
  const rows = [...summary.rating_counts].sort((a, b) => b.rating - a.rating);

  return (
    <div className="flex flex-col gap-6 rounded-xl border bg-card p-6 text-sm">
      {/* S2-BR-003 and S2-BR-004: how many reviews, and their average rating. */}
      <dl className="grid grid-cols-2 gap-4">
        <div>
          <dt className="text-muted-foreground">Reviews</dt>
          <dd className="text-xl font-semibold">{reviewCount(summary.review_count)}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Average rating</dt>
          {/* One decimal, as Google Maps shows it. */}
          <dd className="text-xl font-semibold">{summary.average_rating?.toFixed(1)} out of 5</dd>
        </div>
      </dl>

      {/* S2-BR-004: how many reviews gave each rating. The count is always shown as text;
          the bar only illustrates it. */}
      <table className="w-full">
        <caption className="sr-only">Reviews by rating</caption>
        <thead>
          <tr className="text-muted-foreground">
            <th scope="col" className="pb-2 text-left font-medium">
              Rating
            </th>
            <th scope="col" className="sr-only">
              Share of reviews
            </th>
            <th scope="col" className="pb-2 text-right font-medium">
              Reviews
            </th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ rating, count }) => (
            <tr key={rating}>
              <th scope="row" className="py-1 pr-4 text-left font-normal whitespace-nowrap">
                {rating} {rating === 1 ? "star" : "stars"}
              </th>
              <td className="w-full py-1">
                <div className="h-2 rounded-full bg-muted" aria-hidden="true">
                  <div
                    className="h-2 rounded-full bg-primary"
                    style={{ width: `${(count / summary.review_count) * 100}%` }}
                  />
                </div>
              </td>
              <td className="py-1 pl-4 text-right font-medium tabular-nums">{count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
