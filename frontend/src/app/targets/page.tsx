"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { ingestionLabel, platformLabel, shortSource, type Target } from "@/lib/targets";

export default function TargetsPage() {
  const router = useRouter();
  const [targets, setTargets] = useState<Target[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<{ items: Target[] }>("/api/v1/targets")
      .then((data) => setTargets(data.items))
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          router.replace("/login");
          return;
        }
        setError(e instanceof ApiError ? e.message : "Could not reach the server. Please try again.");
      });
  }, [router]);

  return (
    <main className="mx-auto flex min-h-screen max-w-2xl flex-col gap-6 p-6">
      <div>
        <Link href="/dashboard" className="text-sm underline">
          Back to dashboard
        </Link>
        <div className="mt-2 flex items-center justify-between">
          <h1 className="text-2xl font-semibold">My targets</h1>
          <Link href="/targets/new" className="rounded bg-black px-4 py-2 text-white">
            New target
          </Link>
        </div>
      </div>

      {error && (
        <p role="alert" className="text-red-600">
          {error}
        </p>
      )}
      {!error && targets === null && <p role="status">Loading your targets…</p>}
      {targets?.length === 0 && (
        <p>
          You have no targets yet.{" "}
          <Link href="/targets/new" className="underline">
            Create your first one
          </Link>
          .
        </p>
      )}

      <ul className="flex flex-col gap-3">
        {targets?.map((target) => (
          <li key={target.id}>
            <Link
              href={`/targets/${target.id}`}
              className="flex flex-col gap-1 rounded border p-4 hover:bg-gray-50 dark:hover:bg-white/5"
            >
              <span className="font-medium">{target.name}</span>
              <span className="text-sm text-gray-600 dark:text-gray-400">
                {platformLabel(target.platform)} · {shortSource(target.source_url)}
              </span>
              <span className="text-sm">{ingestionLabel(target.ingestion_status)}</span>
            </Link>
          </li>
        ))}
      </ul>
    </main>
  );
}
