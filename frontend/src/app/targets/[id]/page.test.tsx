// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { IngestionStatus } from "@/lib/ingestion-runs";

const TARGET_ID = "11111111-1111-1111-1111-111111111111";
const RUNS_URL = `/api/v1/targets/${TARGET_ID}/ingestion-runs`;

// lib/api.ts reads this as soon as it is imported.
vi.hoisted(() => {
  process.env.NEXT_PUBLIC_API_URL = "http://api.test";
});

const router = vi.hoisted(() => ({ replace: vi.fn() }));
const refreshTargets = vi.fn(async () => {});

vi.mock("next/navigation", () => ({
  // The same router every render, as in the app, so effects don't re-run.
  useRouter: () => router,
  useParams: () => ({ id: "11111111-1111-1111-1111-111111111111" }),
}));
vi.mock("@/app/_components/app-shell", () => ({
  useTargets: () => ({ refresh: refreshTargets }),
}));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import TargetWorkspacePage from "./page";

function run(status: IngestionStatus) {
  const finished = status === "COMPLETED" || status === "PARTIAL" || status === "FAILED";
  return {
    id: "22222222-2222-2222-2222-222222222222",
    target_id: TARGET_ID,
    job_type: "INGESTION",
    status,
    queued_at: "2026-11-02T12:00:00Z",
    started_at: "2026-11-02T12:00:00Z",
    finished_at: finished ? "2026-11-02T12:01:00Z" : null,
    reviews_ingested: 0,
    reviews_rejected: 0,
    reviews_duplicate: 0,
    error_code: status === "FAILED" ? "TIMEOUT" : null,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function apiError(status: number, code: string, message: string) {
  return json({ error: { code, message, details: [] } }, status);
}

type Backend = {
  latestRuns: (ReturnType<typeof run> | null)[];
  start?: () => Response | Promise<Response>;
};

// Answers the workspace's requests. Each latest-run request uses the next entry in
// latestRuns, repeating the last one, so a test can make a run appear between requests.
function fakeBackend({ latestRuns, start }: Backend) {
  const remaining = [...latestRuns];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input)).pathname;
    if (url === RUNS_URL && init?.method === "POST") {
      if (!start) throw new Error("This test did not expect a run to start.");
      return start();
    }
    if (url === `${RUNS_URL}/latest`) {
      const next = remaining.length > 1 ? remaining.shift() : remaining[0];
      return json({ run: next ?? null });
    }
    if (url === `/api/v1/targets/${TARGET_ID}/reviews`) return json({ items: [] });
    if (url === `/api/v1/targets/${TARGET_ID}`) {
      return json({
        id: TARGET_ID,
        name: "Joe's Pizza",
        platform: "google_maps",
        source_url: "https://www.google.com/maps/place/Joes+Pizza",
        ingestion_status: latestRuns[0]?.status ?? null,
        created_at: "2026-11-01T12:00:00Z",
        updated_at: "2026-11-01T12:00:00Z",
      });
    }
    throw new Error(`Unexpected request: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function startRequests(fetchMock: ReturnType<typeof fakeBackend>) {
  return fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
}

// Waits for the page's first requests to finish, so a click can't race with them.
async function renderWorkspace(statusLabel: string) {
  render(<TargetWorkspacePage />);
  await screen.findByRole("heading", { name: "Joe's Pizza" });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  await screen.findByText(statusLabel);
}

function ingestButtons() {
  return screen.getAllByRole<HTMLButtonElement>("button", { name: "Ingest reviews" });
}

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("while a run is active", () => {
  it.each([
    ["QUEUED", "Queued"],
    ["RUNNING", "Ingesting…"],
  ] as const)("the ingest button is disabled when the run is %s", async (status, label) => {
    fakeBackend({ latestRuns: [run(status)] });

    await renderWorkspace(label);

    for (const button of ingestButtons()) expect(button.disabled).toBe(true);
  });
});

describe("when no run is active", () => {
  it("a target that was never ingested can start one", async () => {
    const fetchMock = fakeBackend({
      latestRuns: [null],
      start: () => json(run("QUEUED"), 202),
    });
    await renderWorkspace("Not ingested");
    const [button] = ingestButtons();
    expect(button.disabled).toBe(false);

    fireEvent.click(button);

    await screen.findByText("Queued");
    expect(startRequests(fetchMock)).toHaveLength(1);
    for (const each of ingestButtons()) expect(each.disabled).toBe(true);
  });

  it("the button shows it is working until the request finishes", async () => {
    let finish: (response: Response) => void = () => {};
    fakeBackend({
      latestRuns: [null],
      start: () => new Promise<Response>((resolve) => (finish = resolve)),
    });
    await renderWorkspace("Not ingested");

    fireEvent.click(ingestButtons()[0]);

    const working = await screen.findAllByRole<HTMLButtonElement>("button", {
      name: "Starting…",
    });
    for (const button of working) expect(button.disabled).toBe(true);
    finish(json(run("QUEUED"), 202));
    await screen.findByText("Queued");
  });

  it("try again on a failed run starts a new one", async () => {
    const fetchMock = fakeBackend({
      latestRuns: [run("FAILED")],
      start: () => json(run("QUEUED"), 202),
    });
    await renderWorkspace("Failed");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await screen.findByText("Queued");
    expect(startRequests(fetchMock)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("starting a run reloads the sidebar's statuses", async () => {
    fakeBackend({ latestRuns: [null], start: () => json(run("QUEUED"), 202) });
    await renderWorkspace("Not ingested");
    refreshTargets.mockClear();

    fireEvent.click(ingestButtons()[0]);

    await waitFor(() => expect(refreshTargets).toHaveBeenCalled());
  });
});

describe("when starting a run is refused", () => {
  it("a 409 shows the message and the run that is already active", async () => {
    const message = "Reviews are already being collected for this target.";
    fakeBackend({
      latestRuns: [null, run("RUNNING")],
      start: () => apiError(409, "CONFLICT", message),
    });
    await renderWorkspace("Not ingested");

    fireEvent.click(ingestButtons()[0]);

    await screen.findByText(message);
    await screen.findByText("Ingesting…");
    for (const button of ingestButtons()) expect(button.disabled).toBe(true);
  });

  it("a 401 redirects to /login", async () => {
    fakeBackend({
      latestRuns: [null],
      start: () => apiError(401, "UNAUTHENTICATED", "Please sign in."),
    });
    await renderWorkspace("Not ingested");

    fireEvent.click(ingestButtons()[0]);

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  });

  it("a 404 shows the not found page", async () => {
    fakeBackend({
      latestRuns: [null],
      start: () => apiError(404, "NOT_FOUND", "Target not found."),
    });
    await renderWorkspace("Not ingested");

    fireEvent.click(ingestButtons()[0]);

    await screen.findByRole("heading", { name: "Target not found" });
  });

  it("a network failure shows a message and allows another try", async () => {
    fakeBackend({
      latestRuns: [null],
      start: () => {
        throw new TypeError("Failed to fetch");
      },
    });
    await renderWorkspace("Not ingested");

    fireEvent.click(ingestButtons()[0]);

    await screen.findByText("Could not reach the server. Please try again.");
    await waitFor(() => expect(ingestButtons()[0].disabled).toBe(false));
  });
});
