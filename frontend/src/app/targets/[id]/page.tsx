"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { ingestionLabel, platformLabel, type Target } from "@/lib/targets";

export default function TargetWorkspacePage() {
  const router = useRouter();
  const { id } = useParams<{ id: string }>();
  const [target, setTarget] = useState<Target | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<Target>(`/api/v1/targets/${id}`)
      .then(setTarget)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          router.replace("/login");
        } else if (e instanceof ApiError && (e.status === 404 || e.status === 422)) {
          // Another user's target looks exactly like a missing one.
          setNotFound(true);
        } else {
          setError(e instanceof ApiError ? e.message : "Could not reach the server. Please try again.");
        }
      });
  }, [id, router]);

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
          <a href={target.source_url} target="_blank" rel="noopener noreferrer" className="underline">
            {target.source_url}
          </a>
        </dd>
        <dt className="text-gray-600 dark:text-gray-400">Ingestion</dt>
        <dd>{ingestionLabel(target.ingestion_status)}</dd>
      </dl>

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
