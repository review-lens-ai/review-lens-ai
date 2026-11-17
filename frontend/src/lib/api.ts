import { createClient } from "@/lib/supabase/client";

const API_URL = process.env.NEXT_PUBLIC_API_URL;
if (!API_URL) {
  throw new Error(
    "NEXT_PUBLIC_API_URL is not set. Copy frontend/.env.example to frontend/.env.local.",
  );
}

export type FieldIssue = { field: string; message: string };

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: FieldIssue[] = [],
  ) {
    super(message);
  }
}

// The error type of every generated hook (src/lib/generated/api.ts): what apiFetch throws.
// An ApiError when the backend answers with an error; a plain Error (TypeError) when the
// request never reaches it. Check with `instanceof ApiError` before reading status or code.
// Orval looks for this export by name; Body is the documented error body, already in ApiError.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
export type ErrorType<Body> = Error;

// What to tell the user about a failed request.
export function errorMessage(error: unknown): string {
  return error instanceof ApiError
    ? error.message
    : "Could not reach the server. Please try again.";
}

// The session ended. Handled once for every request, by the QueryProvider (redirect to /login).
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

// 404, or 422 for a malformed id: both mean "no such target" to the user. Another user's
// target is also a 404, so it looks exactly like a missing one.
export function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 422);
}

// Every backend call sends the Supabase access token; the backend verifies it.
export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const { data } = await createClient().auth.getSession();
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      ...init.headers,
      ...(data.session ? { Authorization: `Bearer ${data.session.access_token}` } : {}),
    },
  });
  const body = await res.json().catch(() => null);
  if (!res.ok) {
    throw new ApiError(
      res.status,
      body?.error?.code ?? "UNKNOWN",
      body?.error?.message ?? "Something went wrong. Please try again.",
      body?.error?.details ?? [],
    );
  }
  return body as T;
}
