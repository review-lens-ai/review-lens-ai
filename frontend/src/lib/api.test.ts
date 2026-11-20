import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiErrorResponse, jsonResponse } from "@/test/responses";
import type { ApiError as ApiErrorType } from "./api";

const getSession = vi.fn();
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession } }),
}));

async function loadApi() {
  vi.resetModules();
  return import("./api");
}

describe("apiFetch", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    getSession.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("sends the signed-in user's access token and returns the JSON body", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "tok-123" } } });
    fetchMock.mockResolvedValue(jsonResponse({ user_id: "u1" }));
    const { apiFetch } = await loadApi();

    const result = await apiFetch<{ user_id: string }>("/api/v1/me");

    expect(result).toEqual({ user_id: "u1" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/v1/me");
    expect(init.headers.Authorization).toBe("Bearer tok-123");
  });

  it("sends no Authorization header when nobody is signed in", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    fetchMock.mockResolvedValue(jsonResponse({}));
    const { apiFetch } = await loadApi();

    await apiFetch("/api/v1/me");

    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty("Authorization");
  });

  it("turns the backend error format into an ApiError", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: "AUTHENTICATION_REQUIRED", message: "Please sign in to continue." } },
        401,
      ),
    );
    const { apiFetch, ApiError } = await loadApi();

    const failure = (await apiFetch("/api/v1/targets").catch((e) => e)) as ApiErrorType;

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({
      status: 401,
      code: "AUTHENTICATION_REQUIRED",
      message: "Please sign in to continue.",
    });
  });

  it("shows a generic message, not raw server output, when the error is not JSON", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    fetchMock.mockResolvedValue(new Response("<html>Traceback ...</html>", { status: 500 }));
    const { apiFetch } = await loadApi();

    const failure = (await apiFetch("/api/v1/targets").catch((e) => e)) as ApiErrorType;

    expect(failure.status).toBe(500);
    expect(failure.code).toBe("UNKNOWN");
    expect(failure.message).toBe("Something went wrong. Please try again.");
    expect(failure.message).not.toContain("Traceback");
  });

  it("refuses to load when the API address is not configured", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");

    await expect(loadApi()).rejects.toThrow("NEXT_PUBLIC_API_URL is not set");
  });
});

describe("apiFetchFile", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "http://api.test");
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    getSession.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("sends the access token and returns the file with the name the backend chose", async () => {
    getSession.mockResolvedValue({ data: { session: { access_token: "tok-123" } } });
    fetchMock.mockResolvedValue(
      new Response("rating,review_text", {
        headers: { "Content-Disposition": 'attachment; filename="joes-pizza-reviews.csv"' },
      }),
    );
    const { apiFetchFile } = await loadApi();

    const { blob, filename } = await apiFetchFile("/api/v1/targets/t1/export/reviews");

    expect(filename).toBe("joes-pizza-reviews.csv");
    expect(await blob.text()).toBe("rating,review_text");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://api.test/api/v1/targets/t1/export/reviews");
    expect(init.headers.Authorization).toBe("Bearer tok-123");
  });

  it("returns no file name when the backend sends none", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    fetchMock.mockResolvedValue(new Response("x"));
    const { apiFetchFile } = await loadApi();

    expect((await apiFetchFile("/api/v1/x")).filename).toBeNull();
  });

  it("throws the backend's error, as apiFetch does", async () => {
    getSession.mockResolvedValue({ data: { session: null } });
    fetchMock.mockResolvedValue(apiErrorResponse(404, "NOT_FOUND", "Target not found."));
    const { apiFetchFile, ApiError } = await loadApi();

    const error = (await apiFetchFile("/api/v1/x").catch((e) => e)) as ApiErrorType;

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(404);
    expect(error.message).toBe("Target not found.");
  });
});
