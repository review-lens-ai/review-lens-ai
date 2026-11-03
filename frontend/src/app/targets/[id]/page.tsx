"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import {
  CircleAlertIcon,
  CircleCheckIcon,
  ExternalLinkIcon,
  InboxIcon,
  Loader2Icon,
  RefreshCwIcon,
  SearchXIcon,
  XIcon,
} from "lucide-react";
import { useTargets } from "@/app/_components/app-shell";
import { EmptyState } from "@/app/_components/empty-state";
import { PlatformBadge } from "@/app/_components/platform-badge";
import { ReviewCard } from "@/app/_components/review-card";
import { StatusBadge } from "@/app/_components/status-badge";
import { Alert, AlertDescription, AlertTitle } from "@/app/_components/ui/alert";
import { Button } from "@/app/_components/ui/button";
import { Skeleton } from "@/app/_components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/app/_components/ui/tabs";
import { apiFetch, ApiError } from "@/lib/api";
import {
  failureMessage,
  fetchLatestRun,
  formatRunTime,
  isActive,
  reviewCount,
  runDetail,
  startIngestionRun,
  type IngestionRun,
} from "@/lib/ingestion-runs";
import { fetchReviews, REVIEWS_PAGE_SIZE, type Review } from "@/lib/reviews";
import { platformLabel, shortSource, type Target } from "@/lib/targets";

// S1-023: while a run is queued or running, check for updates this often,
// and give up after this long (a stale run is failed by the backend anyway).
const POLL_EVERY_MS = 3_000;
const POLL_LIMIT_MS = 5 * 60_000;

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
  return <TargetWorkspace key={id} id={id} />;
}

function TargetWorkspace({ id }: { id: string }) {
  const router = useRouter();
  const { refresh: refreshTargets } = useTargets();
  const [target, setTarget] = useState<Target | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // S1-023: newest ingestion attempt (null = never ingested).
  const [latestRun, setLatestRun] = useState<IngestionRun | null>(null);
  // null while loading; reviewsFailed keeps a load failure from looking like "no reviews".
  // reviews holds only the pages loaded so far; reviewTotal is all of the target's reviews.
  const [reviews, setReviews] = useState<Review[] | null>(null);
  const [reviewTotal, setReviewTotal] = useState(0);
  const [reviewsFailed, setReviewsFailed] = useState(false);
  const [dismissedRunId, setDismissedRunId] = useState<string | null>(null);
  // S1-019: a start request is on its way, and why the last one was refused.
  const [starting, setStarting] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  async function startIngestion() {
    setStarting(true);
    setStartError(null);
    try {
      // The QUEUED run is active, so the polling below takes over from here.
      setLatestRun(await startIngestionRun(id));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) {
        router.replace("/login");
      } else if (e instanceof ApiError && e.status === 404) {
        setNotFound(true);
      } else if (e instanceof ApiError && e.status === 409) {
        // A run is already queued or running: show it instead of a second one.
        setStartError(e.message);
        fetchLatestRun(id)
          .then(setLatestRun)
          .catch(() => {});
      } else {
        setStartError(
          e instanceof ApiError ? e.message : "Could not reach the server. Please try again.",
        );
      }
    } finally {
      setStarting(false);
    }
  }

  useEffect(() => {
    apiFetch<Target>(`/api/v1/targets/${id}`)
      .then((t) => {
        setTarget(t);
        // Details for the status line. If this one call fails, the badge still
        // shows the status from the target itself, so the page stays usable.
        fetchLatestRun(id)
          .then(setLatestRun)
          .catch(() => setLatestRun(null));
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          router.replace("/login");
        } else if (e instanceof ApiError && (e.status === 404 || e.status === 422)) {
          // Another user's target looks exactly like a missing one.
          setNotFound(true);
        } else {
          setError(
            e instanceof ApiError ? e.message : "Could not reach the server. Please try again.",
          );
        }
      });
  }, [id, router]);

  // The sidebar shows each target's status too, so reload it whenever this run's changes.
  const runStatus = latestRun?.status ?? null;
  useEffect(() => {
    if (runStatus) void refreshTargets();
  }, [runStatus, refreshTargets]);

  // S1-023: keep the status fresh while a run is active, so it moves
  // queued -> running -> finished without a manual refresh. Keyed on the run's
  // id, so a status change within the same run doesn't restart the 5-minute limit.
  const activeRunId = latestRun && isActive(latestRun.status) ? latestRun.id : null;
  useEffect(() => {
    if (!activeRunId) return;
    const pollingSince = Date.now();
    let stopped = false;
    const timer = setInterval(() => {
      if (Date.now() - pollingSince > POLL_LIMIT_MS) {
        clearInterval(timer);
        return;
      }
      fetchLatestRun(id)
        .then((run) => {
          if (!stopped) setLatestRun(run);
        })
        // A failed check keeps the last known status; the next tick tries again.
        .catch(() => {});
    }, POLL_EVERY_MS);
    return () => {
      // Stop when the run finishes or the user leaves the page.
      stopped = true;
      clearInterval(timer);
    };
  }, [id, activeRunId]);

  // A never-ingested target has nothing to load. Otherwise load the reviews, and again
  // whenever a run finishes. Keeps showing earlier reviews during a new run (§7.3).
  const everIngested = Boolean(latestRun ?? target?.ingestion_status);
  const finishedAt = latestRun?.finished_at ?? null;
  useEffect(() => {
    if (!everIngested) return;
    let stopped = false;
    // Always page 1, replacing what was loaded: a finished run shifts the rows (#68).
    fetchReviews(id)
      .then((page) => {
        if (stopped) return;
        setReviews(page.items);
        setReviewTotal(page.total);
        setReviewsFailed(false);
      })
      .catch(() => {
        if (!stopped) setReviewsFailed(true);
      });
    return () => {
      stopped = true;
    };
  }, [id, everIngested, finishedAt]);

  // The next page, after the reviews already loaded. Throws if the request fails, so
  // the Reviews panel can say so and keep the cards it already shows.
  async function loadMoreReviews() {
    const loaded = reviews ?? [];
    const page = await fetchReviews(id, loaded.length);
    // A run that finishes mid-paging can repeat a few reviews on the next page.
    const seen = new Set(loaded.map((review) => review.id));
    const added = page.items.filter((review) => !seen.has(review.id));
    setReviews([...loaded, ...added]);
    // Nothing new means the list ended early, so stop offering more.
    setReviewTotal(added.length > 0 ? page.total : loaded.length);
  }

  if (notFound) {
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

  if (error) {
    return (
      <Alert variant="destructive">
        <CircleAlertIcon aria-hidden="true" />
        <AlertTitle>We couldn&apos;t load this target.</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }

  if (!target) return <WorkspaceSkeleton />;

  // One active run per target, so the buttons are off while one is queued or running.
  const ingest: IngestAction = {
    onStart: startIngestion,
    starting,
    disabled: starting || isActive(latestRun?.status ?? target.ingestion_status),
  };

  // A never-ingested target opens on Ingestion, the first step of the flow (§1);
  // a target with data opens on its Reviews.
  const firstTab = target.ingestion_status ? "reviews" : "ingestion";

  return (
    <Tabs defaultValue={firstTab} className="gap-6">
      <TargetContextHeader
        target={target}
        run={latestRun}
        reviewsKept={reviewTotal}
        ingest={ingest}
      />

      {latestRun?.status === "FAILED" && (
        <RunFailedBanner run={latestRun} reviewsKept={reviewTotal} ingest={ingest} />
      )}
      {latestRun?.status === "COMPLETED" && dismissedRunId !== latestRun.id && (
        <Alert className="border-success/30 bg-success/10">
          <CircleCheckIcon aria-hidden="true" className="text-success" />
          <AlertTitle className="flex items-center justify-between gap-2 font-normal">
            {reviewCount(latestRun.reviews_ingested)} collected from{" "}
            {platformLabel(target.platform)}.
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => setDismissedRunId(latestRun.id)}
              className="ml-auto"
            >
              <XIcon className="size-4" aria-hidden="true" />
            </button>
          </AlertTitle>
        </Alert>
      )}

      {startError && (
        <Alert variant="destructive" className="border-destructive/30 bg-destructive/10">
          <CircleAlertIcon aria-hidden="true" />
          <AlertTitle className="line-clamp-none">{startError}</AlertTitle>
        </Alert>
      )}

      <TabsContent value="ingestion">
        <IngestionPanel target={target} run={latestRun} />
      </TabsContent>
      <TabsContent value="reviews">
        <ReviewsPanel
          everIngested={everIngested}
          reviews={reviews}
          total={reviewTotal}
          failed={reviewsFailed}
          onLoadMore={loadMoreReviews}
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
            ` · ${reviewCount(reviewsKept)} from earlier runs`}
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

// S1-023: a failed run is always visible and never looks like a success (§7.3).
function RunFailedBanner({
  run,
  reviewsKept,
  ingest,
}: {
  run: IngestionRun;
  reviewsKept: number;
  ingest: IngestAction;
}) {
  const message = failureMessage(run.error_code);
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

function IngestionPanel({ target, run }: { target: Target; run: IngestionRun | null }) {
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
    </section>
  );
}

function ReviewsPanel({
  everIngested,
  reviews,
  total,
  failed,
  onLoadMore,
}: {
  everIngested: boolean;
  reviews: Review[] | null;
  total: number;
  failed: boolean;
  onLoadMore: () => Promise<void>;
}) {
  const [visible, setVisible] = useState(REVIEWS_PAGE_SIZE);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);

  // Shows the next few cards, asking the server for the next page first when the
  // reviews already loaded run out.
  async function showMore() {
    const next = visible + REVIEWS_PAGE_SIZE;
    if (reviews && next > reviews.length && reviews.length < total) {
      setLoadingMore(true);
      setMoreFailed(false);
      try {
        await onLoadMore();
      } catch {
        setMoreFailed(true);
        return;
      } finally {
        setLoadingMore(false);
      }
    }
    setVisible(next);
  }

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

  const shown = reviews.slice(0, visible);

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-xl font-semibold">Reviews</h2>
        <p className="text-sm text-muted-foreground">
          Showing {shown.length} of {total}
        </p>
      </div>
      <div className="grid items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
        {shown.map((review) => (
          <ReviewCard key={review.id} review={review} />
        ))}
      </div>
      {moreFailed && (
        <p role="alert" className="text-sm text-destructive">
          We couldn&apos;t load more reviews. Try again in a few minutes.
        </p>
      )}
      {shown.length < total && (
        <Button variant="outline" className="w-fit" onClick={showMore} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Show more reviews"}
        </Button>
      )}
    </section>
  );
}
