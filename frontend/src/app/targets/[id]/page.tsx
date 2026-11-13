"use client";

import { Suspense, useState } from "react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import {
  CircleAlertIcon,
  CircleCheckIcon,
  ExternalLinkIcon,
  InboxIcon,
  Loader2Icon,
  RefreshCwIcon,
  SearchXIcon,
  TriangleAlertIcon,
  XIcon,
} from "lucide-react";
import { EmptyState } from "@/app/_components/empty-state";
import { PlatformBadge } from "@/app/_components/platform-badge";
import { ReviewCard } from "@/app/_components/review-card";
import { ReviewSummaryCard } from "@/app/_components/review-summary-card";
import { StatusBadge } from "@/app/_components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/app/_components/ui/alert";
import { Button } from "@/app/_components/ui/button";
import { Skeleton } from "@/app/_components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/app/_components/ui/tabs";
import { errorMessage, isNotFound, isUnauthorized } from "@/lib/api";
import { type SkippedReason, useGetTarget } from "@/lib/generated/api";
import { useLatestRun } from "@/lib/hooks/use-latest-run";
import { useReviews } from "@/lib/hooks/use-reviews";
import {
  formatRunTime,
  isActive,
  reviewCount,
  runDetail,
  runFailureMessage,
  skippedSummary,
  skipReasonLabel,
  type IngestionRun,
} from "@/lib/ingestion-runs";
import { REVIEWS_PAGE_SIZE, type Review } from "@/lib/reviews";
import { platformLabel, shortSource, type Target } from "@/lib/targets";

// S1-019: what the ingest buttons need to start a run.
type IngestAction = { onStart: () => void; starting: boolean; disabled: boolean };

function IngestButton({ ingest }: { ingest: IngestAction }) {
  return (
    <Button onClick={ingest.onStart} disabled={ingest.disabled}>
      {ingest.starting ? (
        <Loader2Icon className="animate-spin" aria-hidden="true" />
      ) : (
        <RefreshCwIcon aria-hidden="true" />
      )}
      {ingest.starting ? "Starting…" : "Ingest reviews"}
    </Button>
  );
}

function WorkspaceSkeleton() {
  return (
    <div role="status" aria-label="Loading target" className="flex flex-col gap-6">
      <Skeleton className="h-52 w-full rounded-xl" />
      <Skeleton className="h-40 w-full rounded-xl" />
    </div>
  );
}

// next.config.ts turns on cacheComponents, so anything that reads the URL at
// runtime (useParams) must sit inside <Suspense>. The page shell renders at build
// time; the workspace fills in once the id is known.
export default function TargetWorkspacePage() {
  return (
    <Suspense fallback={<WorkspaceSkeleton />}>
      <WorkspaceForUrl />
    </Suspense>
  );
}

// The sidebar moves between targets without remounting the page. The key starts each
// target with empty state, so one target's reviews or status can never show on another.
function WorkspaceForUrl() {
  const { id } = useParams<{ id: string }>();
  // ?review=<id> opens the Reviews tab on that review (e.g. a Q&A source link).
  const focusReviewId = useSearchParams().get("review");
  return <TargetWorkspace key={id} id={id} focusReviewId={focusReviewId} />;
}

function TargetWorkspace({ id, focusReviewId }: { id: string; focusReviewId: string | null }) {
  // The data, each from its own hook. A 401 from any of them is handled once, by the
  // QueryProvider (redirect to /login).
  const targetQuery = useGetTarget(id);
  const latest = useLatestRun(id);
  const run = latest.run;
  const skippedReasons = latest.skippedReasons;
  // A never-ingested target has no reviews to load.
  const everIngested = Boolean(run ?? targetQuery.data?.ingestion_status);
  const reviews = useReviews(id, {
    enabled: everIngested,
    // Reviews belong to the latest run: a new or finished run reloads them from page 1.
    latestRun: latest.loaded ? run : undefined,
    focusReviewId,
  });
  const [dismissedRunId, setDismissedRunId] = useState<string | null>(null);

  // Another user's target looks exactly like a missing one.
  if (isNotFound(targetQuery.error) || isNotFound(latest.startError)) {
    return (
      <EmptyState
        icon={<SearchXIcon />}
        title="Target not found"
        message="We couldn't find that target. It may have been deleted, or you may not have access."
        action={
          <Button asChild variant="outline">
            <Link href="/targets">Back to my targets</Link>
          </Button>
        }
      />
    );
  }

  if (targetQuery.error && !isUnauthorized(targetQuery.error)) {
    return (
      <Alert variant="destructive">
        <CircleAlertIcon aria-hidden="true" />
        <AlertTitle>We couldn&apos;t load this target.</AlertTitle>
        <AlertDescription>{errorMessage(targetQuery.error)}</AlertDescription>
      </Alert>
    );
  }

  const target = targetQuery.data;
  if (!target) return <WorkspaceSkeleton />;

  // Why the last start was refused. A 401 redirects and a 404 shows "not found" above.
  const startError =
    latest.startError && !isUnauthorized(latest.startError)
      ? errorMessage(latest.startError)
      : null;

  // One active run per target, so the buttons are off while one is queued or running.
  // They are also off once a run found nothing new (see isUpToDate).
  const ingest: IngestAction = {
    onStart: latest.start,
    starting: latest.starting,
    disabled:
      latest.starting || isActive(run?.status ?? target.ingestion_status) || isUpToDate(run),
  };

  // A never-ingested target opens on Ingestion, the first step of the flow (§1);
  // a target with data opens on its Reviews.
  const firstTab = target.ingestion_status || focusReviewId ? "reviews" : "ingestion";

  return (
    <Tabs defaultValue={firstTab} className="gap-6">
      <TargetContextHeader target={target} run={run} reviewsKept={reviews.total} ingest={ingest} />

      {run?.status === "FAILED" && (
        <RunFailedBanner
          run={run}
          reasons={skippedReasons}
          reviewsKept={reviews.total}
          ingest={ingest}
        />
      )}
      {run?.status === "COMPLETED" && dismissedRunId !== run.id && (
        <Alert className="border-success/30 bg-success/10">
          <CircleCheckIcon aria-hidden="true" className="text-success" />
          <AlertTitle className="flex items-center justify-between gap-2 font-normal">
            {completedMessage(
              run,
              platformLabel(target.platform),
              reviews.reviews === null ? null : reviews.total,
            )}
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => setDismissedRunId(run.id)}
              className="ml-auto"
            >
              <XIcon className="size-4" aria-hidden="true" />
            </button>
          </AlertTitle>
        </Alert>
      )}

      {run?.status === "PARTIAL" && dismissedRunId !== run.id && (
        <Alert className="border-warning/30 bg-warning/10">
          <TriangleAlertIcon aria-hidden="true" className="text-warning" />
          <AlertTitle className="flex items-center justify-between gap-2 font-normal">
            {partialMessage(
              run,
              platformLabel(target.platform),
              reviews.reviews === null ? null : reviews.total,
            )}
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => setDismissedRunId(run.id)}
              className="ml-auto"
            >
              <XIcon className="size-4" aria-hidden="true" />
            </button>
          </AlertTitle>
          {/* S2-007: why records were skipped, and that they are not in the dataset. */}
          {skippedReasons.length > 0 && (
            <AlertDescription>
              Skipped: {skippedSummary(skippedReasons)}. These records were not added to your
              reviews.
            </AlertDescription>
          )}
        </Alert>
      )}

      {startError && (
        <Alert variant="destructive" className="border-destructive/30 bg-destructive/10">
          <CircleAlertIcon aria-hidden="true" />
          <AlertTitle className="line-clamp-none">{startError}</AlertTitle>
        </Alert>
      )}

      <TabsContent value="ingestion" className="flex flex-col gap-8">
        {/* S2-006: the numbers behind the result below, from the saved reviews. Not shown
            for a never-ingested target, where the panel below already says so. */}
        {everIngested && (
          <ReviewSummaryCard
            targetId={id}
            targetName={target.name}
            platform={platformLabel(target.platform)}
            latestRun={latest.loaded ? run : undefined}
          />
        )}
        <IngestionPanel target={target} run={run} reasons={skippedReasons} />
      </TabsContent>
      <TabsContent value="reviews">
        <ReviewsPanel
          everIngested={everIngested}
          reviews={reviews.reviews}
          total={reviews.total}
          page={reviews.page}
          failed={reviews.failed}
          pageLoading={reviews.pageLoading}
          pageFailed={reviews.pageFailed}
          onPage={reviews.showPage}
          highlightId={focusReviewId}
        />
      </TabsContent>
    </Tabs>
  );
}

// TargetContextHeader (ui-ux-standards.md §3.1): always above workspace content, so the
// user knows which target is active.
function TargetContextHeader({
  target,
  run,
  reviewsKept,
  ingest,
}: {
  target: Target;
  run: IngestionRun | null;
  reviewsKept: number;
  ingest: IngestAction;
}) {
  const status = run?.status ?? target.ingestion_status;

  return (
    <div className="overflow-hidden rounded-xl border bg-card">
      <div className="flex flex-wrap items-start justify-between gap-4 p-6">
        <div className="flex min-w-0 flex-col gap-2">
          <p className="text-sm text-muted-foreground">Active target</p>
          <h1 className="text-3xl font-semibold tracking-tight">{target.name}</h1>
          <div className="flex flex-wrap items-center gap-3">
            <PlatformBadge platform={target.platform} />
            <a
              href={target.source_url}
              target="_blank"
              rel="noopener noreferrer"
              title={target.source_url}
              className="inline-flex min-w-0 items-center gap-1 font-mono text-xs text-primary underline"
            >
              <span className="truncate">{shortSource(target.source_url)}</span>
              <ExternalLinkIcon className="size-3 shrink-0" aria-hidden="true" />
            </a>
          </div>
        </div>
        <IngestButton ingest={ingest} />
      </div>

      {/* S1-023: status badge + detail line. aria-live announces status changes. */}
      <div
        aria-live="polite"
        className="flex flex-wrap items-center gap-3 border-y bg-background px-6 py-3 text-sm"
      >
        <StatusBadge status={status} />
        <span>
          {runDetail(run)}
          {run?.status === "FAILED" &&
            reviewsKept > 0 &&
            ` · Showing ${reviewCount(reviewsKept)} from earlier runs`}
        </span>
      </div>

      <TabsList variant="line" className="h-11 px-4">
        <TabsTrigger value="ingestion">Ingestion</TabsTrigger>
        <TabsTrigger value="reviews">Reviews</TabsTrigger>
        {/* Summary and Q&A are Sprint 2; §3.1 says not to build them in Sprint 1. */}
      </TabsList>
    </div>
  );
}

// S1-023: the success banner. "Added", not "new": a run can bring back reviews an earlier
// run saved (skipped as duplicates), and the provider may return older reviews. total is
// everything the target has now, or null until the reviews have loaded.
function completedMessage(run: IngestionRun, platform: string, total: number | null): string {
  if (run.reviews_ingested === 0) {
    return run.reviews_duplicate > 0
      ? `Already up to date: all ${reviewCount(run.reviews_duplicate)} from ${platform} were already saved.`
      : "No reviews added.";
  }
  const added = `${reviewCount(run.reviews_ingested)} added from ${platform}`;
  return total === null ? `${added}.` : `${added} · ${total} in total saved.`;
}

// S2-005: a run that found only reviews we already hold. Running it again would only spend
// provider searches to learn the same thing, so the ingest buttons stay off.
function isUpToDate(run: IngestionRun | null): boolean {
  return run?.status === "COMPLETED" && run.reviews_ingested === 0 && run.reviews_duplicate > 0;
}

// S2-005: a partial run saved reviews but skipped some records. Say how many of each,
// so the skipped ones are never mistaken for ingested reviews (S2-BR-006).
function partialMessage(run: IngestionRun, platform: string, total: number | null): string {
  const added = `${reviewCount(run.reviews_ingested)} added from ${platform}`;
  const skipped = `${run.reviews_rejected} skipped as unusable`;
  return total === null
    ? `${added}, ${skipped}.`
    : `${added}, ${skipped} · ${total} in total saved.`;
}

// S1-023: a failed run is always visible and never looks like a success (§7.3).
function RunFailedBanner({
  run,
  reasons,
  reviewsKept,
  ingest,
}: {
  run: IngestionRun;
  reasons: SkippedReason[];
  reviewsKept: number;
  ingest: IngestAction;
}) {
  // S2-007: when every record was skipped, say so (and why) instead of "no reviews".
  const message = runFailureMessage(run, reasons);
  return (
    <Alert variant="destructive" className="border-destructive/30 bg-destructive/10">
      <CircleAlertIcon aria-hidden="true" />
      <AlertTitle>{message.title}</AlertTitle>
      <AlertDescription>
        {message.next}
        {reviewsKept > 0 && ` Your existing ${reviewCount(reviewsKept)} are unchanged.`}
        <Button
          variant="outline"
          size="sm"
          onClick={ingest.onStart}
          disabled={ingest.disabled}
          className="mt-2"
        >
          {ingest.starting ? "Starting…" : "Try again"}
        </Button>
      </AlertDescription>
    </Alert>
  );
}

// S1-023: what each run state means, in words. The badge above already shows the
// short label, so this row explains it instead of repeating it.
const RUN_RESULT: Record<string, string> = {
  QUEUED: "Waiting for the worker to start",
  RUNNING: "Collecting reviews now",
  COMPLETED: "Every review was saved",
  PARTIAL: "Saved, with some reviews skipped as unusable",
  FAILED: "Did not finish (see the message above)",
};

function startedText(run: IngestionRun): string {
  if (run.started_at) return formatRunTime(run.started_at);
  return run.status === "FAILED" ? "Never started" : "Not started yet";
}

function IngestionPanel({
  target,
  run,
  reasons,
}: {
  target: Target;
  run: IngestionRun | null;
  reasons: SkippedReason[];
}) {
  if (!run) {
    return (
      <EmptyState
        icon={<InboxIcon />}
        title="No ingestion runs yet"
        message={`Use Ingest reviews above to collect reviews from ${platformLabel(target.platform)}. Each run's progress, counts and any error will show here.`}
      />
    );
  }

  const rows: [string, React.ReactNode][] = [
    ["Result", RUN_RESULT[run.status] ?? "Unknown"],
    ["Source", platformLabel(target.platform)],
    ["Added to queue", formatRunTime(run.queued_at)],
    // A run that failed before the worker picked it up never started, and never will.
    ["Started", startedText(run)],
    ["Finished", run.finished_at ? formatRunTime(run.finished_at) : "Not finished yet"],
    ["Reviews saved", run.reviews_ingested],
    ["Skipped as unusable", run.reviews_rejected],
    ["Already saved earlier", run.reviews_duplicate],
  ];

  return (
    <section className="flex flex-col gap-4">
      <h2 className="text-xl font-semibold">Latest run</h2>
      <dl className="grid max-w-xl grid-cols-[auto_1fr] gap-x-8 gap-y-3 rounded-xl border bg-card p-6 text-sm">
        {rows.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-muted-foreground">{label}</dt>
            <dd className="font-medium">{value}</dd>
          </div>
        ))}
      </dl>
      {/* S2-007: why this run skipped records. Skipped records are not reviews. */}
      {reasons.length > 0 && (
        <div className="max-w-xl rounded-xl border bg-card p-6 text-sm">
          <h3 className="font-semibold">Why records were skipped</h3>
          <p className="mt-1 text-muted-foreground">
            These records were not added to your reviews.
          </p>
          <table className="mt-4 w-full">
            <thead>
              <tr className="text-xs text-muted-foreground">
                <th scope="col" className="pb-2 text-left font-medium">
                  Reason
                </th>
                <th scope="col" className="pb-2 text-right font-medium">
                  Records
                </th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {reasons.map(({ reason, count }) => {
                const label = skipReasonLabel(reason);
                return (
                  <tr key={reason}>
                    <td className="py-2 pr-4">{label.charAt(0).toUpperCase() + label.slice(1)}</td>
                    <td className="py-2 text-right font-medium tabular-nums">{count}</td>
                  </tr>
                );
              })}
            </tbody>
            {/* With more than one reason, show the total so the numbers add up. */}
            {reasons.length > 1 && (
              <tfoot>
                <tr className="border-t font-medium">
                  <td className="pt-2 pr-4">Total skipped</td>
                  <td className="pt-2 text-right tabular-nums">
                    {reasons.reduce((sum, r) => sum + r.count, 0)}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}
    </section>
  );
}

function ReviewsPanel({
  everIngested,
  reviews,
  total,
  page,
  failed,
  pageLoading,
  pageFailed,
  onPage,
  highlightId,
}: {
  everIngested: boolean;
  reviews: Review[] | null;
  total: number;
  page: number;
  failed: boolean;
  pageLoading: boolean;
  pageFailed: boolean;
  onPage: (page: number) => void;
  highlightId: string | null;
}) {
  if (!everIngested || (reviews !== null && total === 0)) {
    return (
      // The one Ingest button lives in the header above, so this only points to it.
      <EmptyState
        icon={<InboxIcon />}
        message="No reviews yet. Use Ingest reviews above to collect them from the source URL."
      />
    );
  }

  if (failed && !reviews) {
    return (
      <Alert variant="destructive">
        <CircleAlertIcon aria-hidden="true" />
        <AlertTitle>We couldn&apos;t load the reviews.</AlertTitle>
        <AlertDescription>Reload the page or try again in a few minutes.</AlertDescription>
      </Alert>
    );
  }

  if (!reviews) {
    return (
      <div role="status" aria-label="Loading reviews" className="grid gap-4 md:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-40 rounded-xl" />
        ))}
      </div>
    );
  }

  // e.g. "Showing 13–24 of 60": the range is this page, the total is every review.
  const first = page * REVIEWS_PAGE_SIZE + 1;
  const last = page * REVIEWS_PAGE_SIZE + reviews.length;
  const hasPrevious = page > 0;
  const hasNext = last < total;

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xl font-semibold">Reviews</h2>
        <p className="text-sm text-muted-foreground">
          Showing {first}–{last} of {total}
        </p>
      </div>
      <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
        {reviews.map((review) => (
          <ReviewCard
            key={review.id}
            review={review}
            highlighted={review.id === highlightId}
          />
        ))}
      </div>
      {pageFailed && (
        <p role="alert" className="text-sm text-destructive">
          We couldn&apos;t load that page of reviews. Try again in a few minutes.
        </p>
      )}
      {(hasPrevious || hasNext) && (
        <nav aria-label="Review pages" className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => onPage(page - 1)}
            disabled={!hasPrevious || pageLoading}
          >
            Previous
          </Button>
          <Button
            variant="outline"
            onClick={() => onPage(page + 1)}
            disabled={!hasNext || pageLoading}
          >
            Next
          </Button>
        </nav>
      )}
    </section>
  );
}
