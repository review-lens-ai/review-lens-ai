"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { SUPPORTED_PLATFORM_LABEL, type Target } from "@/lib/targets";

type FieldErrors = { name?: string; source_url?: string; form?: string };

export default function NewTargetPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [sourceUrl, setSourceUrl] = useState("");
  const [errors, setErrors] = useState<FieldErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [created, setCreated] = useState<Target | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setErrors({});
    try {
      const target = await apiFetch<Target>("/api/v1/targets", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, source_url: sourceUrl }),
      });
      setCreated(target);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      if (err instanceof ApiError && err.code === "VALIDATION_ERROR") {
        const next: FieldErrors = {};
        for (const issue of err.details) {
          if (issue.field === "name") next.name = issue.message;
          else if (issue.field === "source_url") next.source_url = issue.message;
          else next.form = issue.message;
        }
        setErrors(next);
      } else {
        setErrors({
          form: err instanceof ApiError ? err.message : "Could not reach the server. Please try again.",
        });
      }
    } finally {
      setSubmitting(false);
    }
  }

  function reset() {
    setCreated(null);
    setName("");
    setSourceUrl("");
  }

  if (created) {
    return (
      <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-4 p-6">
        <h1 className="text-2xl font-semibold">Target created</h1>
        <p role="status">
          <strong>{created.name}</strong> was added. Next, open its workspace to collect reviews.
        </p>
        <div className="flex gap-3">
          <Link href={`/targets/${created.id}`} className="rounded bg-black px-4 py-2 text-white">
            Open workspace
          </Link>
          <button type="button" onClick={reset} className="rounded border px-4 py-2">
            Create another
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-6 p-6">
      <div>
        <Link href="/dashboard" className="text-sm underline">
          Back to dashboard
        </Link>
        <h1 className="mt-2 text-2xl font-semibold">New analysis target</h1>
        <p className="text-sm text-gray-500">
          A target is the business or place whose {SUPPORTED_PLATFORM_LABEL} reviews you want to analyze.
        </p>
      </div>

      <form onSubmit={onSubmit} noValidate className="flex flex-col gap-4">
        <label className="flex flex-col gap-1 text-sm">
          Name
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            aria-invalid={Boolean(errors.name)}
            aria-describedby={errors.name ? "name-error" : undefined}
            placeholder="Joe's Pizza, Newark"
            className="rounded border px-3 py-2"
          />
          {errors.name && (
            <span id="name-error" role="alert" className="text-red-600">
              {errors.name}
            </span>
          )}
        </label>

        <div className="flex flex-col gap-1 text-sm">
          Review platform
          <p className="rounded border bg-gray-50 px-3 py-2 text-gray-700 dark:bg-transparent">
            {SUPPORTED_PLATFORM_LABEL}
          </p>
        </div>

        <label className="flex flex-col gap-1 text-sm">
          {SUPPORTED_PLATFORM_LABEL} link
          <input
            type="url"
            inputMode="url"
            value={sourceUrl}
            onChange={(e) => setSourceUrl(e.target.value)}
            aria-invalid={Boolean(errors.source_url)}
            aria-describedby={errors.source_url ? "source-url-error" : undefined}
            placeholder="https://www.google.com/maps/place/..."
            className="rounded border px-3 py-2"
          />
          {errors.source_url && (
            <span id="source-url-error" role="alert" className="text-red-600">
              {errors.source_url}
            </span>
          )}
        </label>

        {errors.form && (
          <p role="alert" className="text-sm text-red-600">
            {errors.form}
          </p>
        )}

        <button
          type="submit"
          disabled={submitting}
          className="rounded bg-black px-4 py-2 text-white disabled:opacity-50"
        >
          {submitting ? "Creating…" : "Create target"}
        </button>
      </form>
    </main>
  );
}
