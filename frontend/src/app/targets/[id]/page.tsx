"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { platformLabel, type Target } from "@/lib/targets";
import {
  failureMessage,
  fetchLatestRun,
  isActive,
  runDetail,
  type IngestionRun,
} from "@/lib/ingestion-runs";
import { StatusBadge } from "@/app/_components/status-badge";

// S1-023: while a run is queued or running, check for updates this often,
// and give up after this long (a stale run is failed by the backend anyway).
const POLL_EVERY_MS = 3_000;
const POLL_LIMIT_MS = 5 * 60_000;

// next.config.ts turns on cacheComponents, so anything that reads the URL at
// runtime (useParams) must sit inside <Suspense>. The page shell renders at build
// time; the workspace fills in once the id is known.
export default function TargetWorkspacePage() {
  return (
    <Suspense
      fallback={
        <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center p-6">
          <p role="status">Loading target…</p>
        </main>
      }
    >
      <TargetWorkspace />
    </Suspense>
  );
}

function TargetWorkspace() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const [target, setTarget] = useState<Target | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // S1-023: newest ingestion attempt (null = never ingested).
  const [latestRun, setLatestRun] = useState<IngestionRun | null>(null);

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

  const header = (
    <Link href="/targets" className="text-sm underline">
      Back to my targets
    </Link>
  );

  if (notFound) {
    return (
      <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-4 p-6">
        {header}
        <h1 className="text-2xl font-semibold">Target not found</h1>
        <p>This target doesn&apos;t exist, or it isn&apos;t yours to open.</p>
      </main>
    );
  }

  if (error) {
    return (
      <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-4 p-6">
        {header}
        <p role="alert" className="text-red-600">
          {error}
        </p>
      </main>
    );
  }

  if (!target) {
    return (
      <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center p-6">
        <p role="status">Loading target…</p>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 p-6">
      <div>
        {header}
        <h1 className="mt-2 text-2xl font-semibold">{target.name}</h1>
        <p className="text-sm text-gray-600 dark:text-gray-400">Analysis workspace</p>
      </div>

      <dl className="grid grid-cols-[8rem_1fr] gap-x-4 gap-y-3 rounded border p-4 text-sm">
        <dt className="text-gray-600 dark:text-gray-400">Review platform</dt>
        <dd>{platformLabel(target.platform)}</dd>
        <dt className="text-gray-600 dark:text-gray-400">Source</dt>
        <dd className="break-all">
          <a
            href={target.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="underline"
          >
            {target.source_url}
          </a>
        </dd>
        <dt className="text-gray-600 dark:text-gray-400">Ingestion</dt>
        {/* S1-023: status badge + detail line (ui-ux-standards.md §7.3).
            aria-live lets screen readers announce when the status changes. */}
        <dd aria-live="polite" className="flex flex-wrap items-center gap-2">
          <StatusBadge status={latestRun?.status ?? target.ingestion_status} />
          <span className="text-gray-600 dark:text-gray-400">{runDetail(latestRun)}</span>
        </dd>
      </dl>

      {latestRun?.status === "failed" && (
        // S1-023: a failed run is always visible and never looks like a success (§7.3).
        <div
          role="alert"
          className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-800"
        >
          <p className="font-medium">{failureMessage(latestRun.error_code).title}</p>
          <p>{failureMessage(latestRun.error_code).next}</p>
        </div>
      )}

      <section className="flex flex-col gap-2">
        <h2 className="text-lg font-medium">Collect reviews</h2>
        <p className="text-sm text-gray-600 dark:text-gray-400">
          Reviews are collected from the source link above.
        </p>
        {/* Starting ingestion is S1-019; this button stays disabled until that story lands. */}
        <button
          type="button"
          disabled
          title="Review ingestion arrives in a later step"
          className="w-fit rounded bg-black px-4 py-2 text-white disabled:opacity-50"
        >
          Start ingestion
        </button>
      </section>
    </main>
  );
}
