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
async function authHeaders(): Promise<Record<string, string>> {
  const { data } = await createClient().auth.getSession();
  return data.session ? { Authorization: `Bearer ${data.session.access_token}` } : {};
}

type ErrorBody = {
  error?: { code?: string; message?: string; details?: FieldIssue[] };
} | null;

// The error the backend's error body describes (see ApiError).
function apiErrorFrom(status: number, body: ErrorBody): ApiError {
  return new ApiError(
    status,
    body?.error?.code ?? "UNKNOWN",
    body?.error?.message ?? "Something went wrong. Please try again.",
    body?.error?.details ?? [],
  );
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { ...init.headers, ...(await authHeaders()) },
  });
  const body: ErrorBody = await res.json().catch(() => null);
  if (!res.ok) {
    throw apiErrorFrom(res.status, body);
  }
  return body as T;
}

// A file the backend sends, such as a CSV or Markdown export (src/lib/exports.ts): the
// body, and the file name the backend chose (null if it sent none).
export async function apiFetchFile(path: string): Promise<{ blob: Blob; filename: string | null }> {
  const res = await fetch(`${API_URL}${path}`, { headers: await authHeaders() });
  if (!res.ok) {
    throw apiErrorFrom(res.status, await res.json().catch(() => null));
  }
  const disposition = res.headers.get("Content-Disposition") ?? "";
  return {
    blob: await res.blob(),
    filename: /filename="?([^";]+)"?/.exec(disposition)?.[1] ?? null,
  };
}
