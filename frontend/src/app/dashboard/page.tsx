"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { createClient } from "@/lib/supabase/client";

type Me = { user_id: string; email: string | null };

export default function DashboardPage() {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch<Me>("/api/v1/me")
      .then(setMe)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) {
          router.replace("/login");
          return;
        }
        setError(e instanceof ApiError ? e.message : "Could not reach the server.");
      });
  }, [router]);

  async function logout() {
    await createClient().auth.signOut();
    router.replace("/login");
    router.refresh();
  }

  return (
    <main className="mx-auto flex min-h-screen max-w-xl flex-col justify-center gap-4 p-6">
      <h1 className="text-2xl font-semibold">ReviewLens</h1>
      {error && (
        <p role="alert" className="text-red-600">
          {error}
        </p>
      )}
      {me && (
        <p>
          Signed in as <strong>{me.email}</strong>
          <br />
          <span className="text-sm text-gray-500">Verified by the backend: {me.user_id}</span>
        </p>
      )}
      <button onClick={logout} className="w-fit rounded border px-4 py-2">
        Log out
      </button>
    </main>
  );
}
