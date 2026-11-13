// Integration tests for the target workspace page: the Start ingestion and
// Try again buttons (#55). The whole page is rendered, and MSW plays the backend.
import { describe, expect, it, vi } from "vitest";
import type { SkippedReason } from "@/lib/generated/api";
import type { IngestionRun } from "@/lib/ingestion-runs";
import {
  act,
  API,
  apiErrorResponse,
  fireEvent,
  http,
  HttpResponse,
  ingestionRun,
  jsonResponse,
  queryWrapper,
  render,
  review,
  reviewPage,
  screen,
  server,
  target,
  waitFor,
  within,
} from "@/test";

const TARGET_ID = "11111111-1111-1111-1111-111111111111";
const TARGET_URL = `${API}/api/v1/targets/${TARGET_ID}`;
const REVIEWS_URL = `${TARGET_URL}/reviews`;
const RUNS_URL = `${TARGET_URL}/ingestion-runs`;
const LATEST_RUN_URL = `${RUNS_URL}/latest`;

// --- Replace the parts of the app this page uses but these tests don't cover ---

// One router object for every render, as in the app, so the page's effects don't re-run.
const router = { replace: vi.fn() };
const refreshSidebar = vi.fn(async () => {});

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useParams: () => ({ id: TARGET_ID }),
}));
vi.mock("@/lib/hooks/use-targets", () => ({
  useTargets: () => ({ refresh: refreshSidebar }),
}));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import TargetWorkspacePage from "./page";

// --- Test data ---

const queuedRun = ingestionRun({
  target_id: TARGET_ID,
  status: "QUEUED",
  started_at: null,
  finished_at: null,
});
const runningRun = ingestionRun({ target_id: TARGET_ID, status: "RUNNING", finished_at: null });
const failedRun = ingestionRun({ target_id: TARGET_ID, status: "FAILED", error_code: "TIMEOUT" });

// --- Helpers ---

// Sets up the endpoints the page calls when it opens.
// latestRun is the newest ingestion run, or null if the target was never ingested.
// skippedReasons says why that run skipped records (S2-007); none by default.
function backendHas(latestRun: IngestionRun | null, skippedReasons: SkippedReason[] = []) {
  let status: IngestionRun["status"] | null = null;
  if (latestRun !== null) {
    status = latestRun.status;
  }

  server.use(
    http.get(TARGET_URL, () => jsonResponse(target({ id: TARGET_ID, ingestion_status: status }))),
    http.get(REVIEWS_URL, () => jsonResponse(reviewPage([]))),
    http.get(LATEST_RUN_URL, () =>
      jsonResponse({ run: latestRun, skipped_reasons: skippedReasons }),
    ),
  );
}

// Sets what the backend answers when the page asks to start a run.
// Returns a mock, so a test can check how many times a run was started.
function startingARunReturns(respond: () => Response | Promise<Response>) {
  const startRun = vi.fn(respond);
  server.use(http.post(RUNS_URL, startRun));
  return startRun;
}

// Renders the page and waits until it has finished loading.
async function openWorkspace(expectedStatus: string) {
  // As in the app: a 401 from any request goes to /login (QueryProvider does this there).
  render(<TargetWorkspacePage />, { wrapper: queryWrapper(() => router.replace("/login")) });
  await screen.findByRole("heading", { name: "Joe's Pizza" });
  await screen.findByText(expectedStatus);

  // Let the page's other first requests finish, so a click doesn't race with them.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

// The "Ingest reviews" button(s) on the page (the header has the only one today).
function ingestButtons() {
  return screen.getAllByRole<HTMLButtonElement>("button", { name: "Ingest reviews" });
}

function expectAllDisabled(buttons: HTMLButtonElement[]) {
  for (const button of buttons) {
    expect(button.disabled).toBe(true);
  }
}

// --- Tests ---

describe("while a run is active", () => {
  it("the ingest button is disabled when the run is queued", async () => {
    backendHas(queuedRun);

    await openWorkspace("Queued");

    expectAllDisabled(ingestButtons());
  });

  it("the ingest button is disabled when the run is running", async () => {
    backendHas(runningRun);

    await openWorkspace("Ingesting…");

    expectAllDisabled(ingestButtons());
  });
});

// The status moves queued -> running -> finished without a manual reload.
// Only the interval timer is faked, so the checks happen at once instead of every 3 s.
describe("checking an active run", () => {
  function runStartedAgo(minutes: number, status: IngestionRun["status"] = "RUNNING") {
    const startedAt = new Date(Date.now() - minutes * 60_000).toISOString();
    return ingestionRun({
      target_id: TARGET_ID,
      status,
      queued_at: startedAt,
      started_at: startedAt,
      finished_at: null,
    });
  }

  // Answers the latest-run checks with `respond`, and counts them.
  function latestRunAnswers(respond: () => Response) {
    const checks = vi.fn(respond);
    server.use(http.get(LATEST_RUN_URL, checks));
    return checks;
  }

  it("checks a running run until it finishes, then stops", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      backendHas(runStartedAgo(1));
      await openWorkspace("Ingesting…");
      const checks = latestRunAnswers(() =>
        jsonResponse({ run: ingestionRun({ target_id: TARGET_ID, reviews_ingested: 3 }) }),
      );

      await act(() => vi.advanceTimersByTimeAsync(3_000));
      await screen.findByText("Ingested");
      const checksUntilFinished = checks.mock.calls.length;

      await act(() => vi.advanceTimersByTimeAsync(30_000));
      expect(checksUntilFinished).toBeGreaterThan(0);
      expect(checks.mock.calls.length).toBe(checksUntilFinished);
    } finally {
      vi.useRealTimers();
    }
  });

  it("stops checking a run that has been going for over 5 minutes", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      const stuck = runStartedAgo(6);
      backendHas(stuck);
      const checks = latestRunAnswers(() => jsonResponse({ run: stuck }));
      await openWorkspace("Ingesting…");
      const checksOnOpen = checks.mock.calls.length;

      await act(() => vi.advanceTimersByTimeAsync(30_000));

      expect(checks.mock.calls.length).toBe(checksOnOpen);
    } finally {
      vi.useRealTimers();
    }
  });

  it("while a new run is going, the list still has what the previous run saved", async () => {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
    try {
      // Run A is going; the target has 5 reviews so far.
      let saved = 5;
      backendHas({ ...runStartedAgo(1), id: "run-a" });
      server.use(
        http.get(REVIEWS_URL, () =>
          jsonResponse(
            reviewPage(
              Array.from({ length: saved }, () => review()),
              { total: saved },
            ),
          ),
        ),
      );
      await openWorkspace("Ingesting…");
      await screen.findByText("Showing 1–5 of 5");

      // Run A finishes and saves 3 more.
      saved = 8;
      latestRunAnswers(() =>
        jsonResponse({
          run: ingestionRun({ id: "run-a", target_id: TARGET_ID, reviews_ingested: 3 }),
        }),
      );
      await act(() => vi.advanceTimersByTimeAsync(3_000));
      await screen.findByText("Showing 1–8 of 8");

      // Run B starts. Its reviews list must not fall back to the one from before run A finished.
      startingARunReturns(() => jsonResponse({ ...queuedRun, id: "run-b" }, 202));
      fireEvent.click(ingestButtons()[0]);
      await screen.findByText("Queued");
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      expect(screen.getByText("Showing 1–8 of 8")).toBeTruthy();
      expect(screen.queryByText("Showing 1–5 of 5")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("when no run is active", () => {
  it("a target that was never ingested can start a run", async () => {
    backendHas(null);
    const startRun = startingARunReturns(() => jsonResponse(queuedRun, 202));
    await openWorkspace("Not ingested");
    expect(ingestButtons()[0].disabled).toBe(false);

    fireEvent.click(ingestButtons()[0]);

    await screen.findByText("Queued");
    expect(startRun).toHaveBeenCalledTimes(1);
    expectAllDisabled(ingestButtons());
  });

  it("the button shows it is working until the request finishes", async () => {
    backendHas(null);
    let finishRequest = () => {};
    startingARunReturns(async () => {
      await new Promise<void>((resolve) => {
        finishRequest = resolve;
      });
      return jsonResponse(queuedRun, 202);
    });
    await openWorkspace("Not ingested");

    fireEvent.click(ingestButtons()[0]);

    const working = await screen.findAllByRole<HTMLButtonElement>("button", {
      name: "Starting…",
    });
    expectAllDisabled(working);
    finishRequest();
    await screen.findByText("Queued");
  });

  it("try again on a failed run starts a new one", async () => {
    backendHas(failedRun);
    const startRun = startingARunReturns(() => jsonResponse(queuedRun, 202));
    await openWorkspace("Failed");

    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await screen.findByText("Queued");
    expect(startRun).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("starting a run reloads the sidebar's statuses", async () => {
    backendHas(null);
    startingARunReturns(() => jsonResponse(queuedRun, 202));
    await openWorkspace("Not ingested");
    refreshSidebar.mockClear();

    fireEvent.click(ingestButtons()[0]);

    await waitFor(() => expect(refreshSidebar).toHaveBeenCalled());
  });
});

describe("when starting a run is refused", () => {
  it("a 409 shows the message and the run that is already active", async () => {
    const message = "Reviews are already being collected for this target.";
    backendHas(null);
    startingARunReturns(() => apiErrorResponse(409, "CONFLICT", message));
    await openWorkspace("Not ingested");
    // Meanwhile, someone else started a run.
    server.use(http.get(LATEST_RUN_URL, () => jsonResponse({ run: runningRun })));

    fireEvent.click(ingestButtons()[0]);

    await screen.findByText(message);
    await screen.findByText("Ingesting…");
    expectAllDisabled(ingestButtons());
  });

  it("a 401 redirects to /login", async () => {
    backendHas(null);
    startingARunReturns(() => apiErrorResponse(401, "UNAUTHENTICATED", "Please sign in."));
    await openWorkspace("Not ingested");

    fireEvent.click(ingestButtons()[0]);

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  });

  it("a 404 shows the not found page", async () => {
    backendHas(null);
    startingARunReturns(() => apiErrorResponse(404, "NOT_FOUND", "Target not found."));
    await openWorkspace("Not ingested");

    fireEvent.click(ingestButtons()[0]);

    await screen.findByRole("heading", { name: "Target not found" });
  });

  it("a network failure shows a message and allows another try", async () => {
    backendHas(null);
    startingARunReturns(() => HttpResponse.error());
    await openWorkspace("Not ingested");

    fireEvent.click(ingestButtons()[0]);

    await screen.findByText("Could not reach the server. Please try again.");
    await waitFor(() => expect(ingestButtons()[0].disabled).toBe(false));
  });
});

// Layout rules from the #59 review (ui-ux-standards.md §1, §3.1, §7.3).
describe("workspace layout", () => {
  it("a never-ingested target opens on the Ingestion tab", async () => {
    backendHas(null);

    await openWorkspace("Not ingested");

    const ingestionTab = screen.getByRole("tab", { name: "Ingestion" });
    expect(ingestionTab.getAttribute("aria-selected")).toBe("true");
    await screen.findByRole("heading", { name: "No ingestion runs yet" });
  });

  it("a target with data opens on the Reviews tab", async () => {
    backendHas(failedRun);

    await openWorkspace("Failed");

    const reviewsTab = screen.getByRole("tab", { name: "Reviews" });
    expect(reviewsTab.getAttribute("aria-selected")).toBe("true");
  });

  it("has exactly one Ingest reviews button", async () => {
    backendHas(null);

    await openWorkspace("Not ingested");

    expect(ingestButtons()).toHaveLength(1);
  });

  it("shows the Ingestion, Reviews and Summary tabs", async () => {
    backendHas(null);

    await openWorkspace("Not ingested");

    const tabs = screen.getAllByRole("tab").map((tab) => tab.textContent);
    expect(tabs).toEqual(["Ingestion", "Reviews", "Summary"]);
  });

  it("the Ingestion tab shows the latest run's counts", async () => {
    backendHas(
      ingestionRun({
        target_id: TARGET_ID,
        status: "PARTIAL",
        reviews_ingested: 38,
        reviews_rejected: 4,
      }),
    );

    await openWorkspace("Partially ingested");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Ingestion" }));

    await screen.findByText("Saved, with some reviews skipped as unusable");
    expect(screen.getByText("38")).toBeTruthy();
    expect(screen.getByText("4")).toBeTruthy();
  });

  it("a run that failed before starting says it never started", async () => {
    backendHas(
      ingestionRun({
        target_id: TARGET_ID,
        status: "FAILED",
        error_code: "QUOTA_EXCEEDED",
        started_at: null,
      }),
    );

    await openWorkspace("Failed");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Ingestion" }));

    await screen.findByText("Never started");
    expect(screen.queryByText("Not started yet")).toBeNull();
  });

  // S2-006: the Summary tab shows the card for this target. The summary is only asked for
  // once the tab is opened, so the other tests here don't need a summary handler.
  it("the Summary tab shows the review count and average for the active target", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, status: "COMPLETED" }));
    const summaryAsked = vi.fn();
    server.use(
      http.get(`${TARGET_URL}/summary`, () => {
        summaryAsked();
        return jsonResponse({
          review_count: 5,
          average_rating: 3.4,
          rating_counts: [1, 1, 0, 1, 2].map((count, i) => ({ rating: i + 1, count })),
        });
      }),
    );

    await openWorkspace("Ingested");
    expect(summaryAsked).not.toHaveBeenCalled();

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Summary" }));

    const card = await screen.findByRole("region", {
      name: "Review summary for Joe's Pizza · Google Maps",
    });
    expect(await within(card).findByText("5 reviews")).toBeTruthy();
    expect(within(card).getByText("3.4 out of 5")).toBeTruthy();
    expect(summaryAsked).toHaveBeenCalledOnce();
  });
});

// The reviews endpoint is paginated (#68): items is one page, total is every review.
describe("reviews list", () => {
  const completedRun = ingestionRun({ target_id: TARGET_ID, status: "COMPLETED" });

  function reviewsNumbered(from: number, count: number) {
    return Array.from({ length: count }, (_, i) =>
      review({ id: `review-${from + i}`, review_text: `Review number ${from + i}` }),
    );
  }

  // The backend holds `total` reviews and answers each request from its limit and
  // offset, like the real endpoint. Returns the offsets asked for, so a test can
  // check which pages were fetched.
  function backendHoldsReviews(total: number) {
    const offsetsAsked: number[] = [];
    server.use(
      http.get(REVIEWS_URL, ({ request }) => {
        const query = new URL(request.url).searchParams;
        const limit = Number(query.get("limit") ?? 50);
        const offset = Number(query.get("offset") ?? 0);
        offsetsAsked.push(offset);
        const size = Math.max(0, Math.min(limit, total - offset));
        return jsonResponse(reviewPage(reviewsNumbered(offset, size), { total, limit, offset }));
      }),
    );
    return offsetsAsked;
  }

  function button(name: string) {
    return screen.getByRole<HTMLButtonElement>("button", { name });
  }

  it("shows the first page and counts every review of the target", async () => {
    backendHas(completedRun);
    backendHoldsReviews(30);

    await openWorkspace("Ingested");

    await screen.findByText("Showing 1–12 of 30");
    expect(screen.getByText("Review number 0")).toBeTruthy();
    expect(screen.queryByText("Review number 12")).toBeNull();
    expect(button("Previous").disabled).toBe(true);
    expect(button("Next").disabled).toBe(false);
  });

  it("next fetches the next page from the server; previous reuses the page already loaded", async () => {
    backendHas(completedRun);
    const offsetsAsked = backendHoldsReviews(30);
    await openWorkspace("Ingested");
    await screen.findByText("Showing 1–12 of 30");

    fireEvent.click(button("Next"));
    await screen.findByText("Showing 13–24 of 30");
    expect(screen.getByText("Review number 12")).toBeTruthy();
    expect(screen.queryByText("Review number 0")).toBeNull();

    fireEvent.click(button("Previous"));
    await screen.findByText("Showing 1–12 of 30");

    // Page 1 is cached for this run (saved reviews only change when a run saves them),
    // so going back to it asks the server for nothing.
    expect(offsetsAsked).toEqual([0, 12]);
  });

  it("next is disabled on the last page", async () => {
    backendHas(completedRun);
    backendHoldsReviews(15);
    await openWorkspace("Ingested");
    await screen.findByText("Showing 1–12 of 15");

    fireEvent.click(button("Next"));

    await screen.findByText("Showing 13–15 of 15");
    await waitFor(() => expect(button("Next").disabled).toBe(true));
    expect(button("Previous").disabled).toBe(false);
  });

  it("a target with one page of reviews has no page buttons", async () => {
    backendHas(completedRun);
    backendHoldsReviews(5);

    await openWorkspace("Ingested");

    await screen.findByText("Showing 1–5 of 5");
    expect(screen.queryByRole("button", { name: "Next" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Previous" })).toBeNull();
  });

  // The list reloads page 1 whenever the run's finished_at changes. Starting a run
  // changes it (to null) without waiting for a poll, so it exercises the same reload
  // a finishing run triggers.
  it("goes back to page 1 when the latest run changes", async () => {
    backendHas(completedRun);
    const offsetsAsked = backendHoldsReviews(30);
    startingARunReturns(() => jsonResponse(queuedRun, 202));
    await openWorkspace("Ingested");
    fireEvent.click(button("Next"));
    await screen.findByText("Showing 13–24 of 30");

    fireEvent.click(ingestButtons()[0]);

    await screen.findByText("Queued");
    await screen.findByText("Showing 1–12 of 30");
    expect(offsetsAsked).toEqual([0, 12, 0]);
  });

  it("the success banner says how many reviews were added and the new total", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, reviews_ingested: 3, reviews_duplicate: 2 }));
    backendHoldsReviews(8);

    await openWorkspace("Ingested");

    await screen.findByText("3 reviews added from Google Maps · 8 in total saved.");
  });

  it("the success banner says when every review was already saved", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, reviews_ingested: 0, reviews_duplicate: 5 }));
    backendHoldsReviews(5);

    await openWorkspace("Ingested");

    await screen.findByText(
      "Already up to date: all 5 reviews from Google Maps were already saved.",
    );
  });

  it("the ingest button is off when the last run found only reviews already saved", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, reviews_ingested: 0, reviews_duplicate: 5 }));
    backendHoldsReviews(5);

    await openWorkspace("Ingested");

    expectAllDisabled(ingestButtons());
  });

  it("the ingest button stays on after a run that added reviews", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, reviews_ingested: 3, reviews_duplicate: 2 }));
    backendHoldsReviews(8);

    await openWorkspace("Ingested");

    for (const button of ingestButtons()) {
      expect(button.disabled).toBe(false);
    }
  });

  // S2-BR-001: the page says which target the status belongs to.
  it("names the active target next to the ingestion status", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, reviews_ingested: 3 }));
    backendHoldsReviews(3);

    await openWorkspace("Ingested");

    expect(screen.getByText("Active target")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Joe's Pizza" })).toBeTruthy();
  });

  // S2-BR-007: the "in total" figure is the size of the stored dataset (what Q&A will read),
  // not the count of the latest run.
  it("reports the stored review total, not just what the latest run added", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, reviews_ingested: 14 }));
    backendHoldsReviews(30);

    await openWorkspace("Ingested");

    await screen.findByText("14 reviews added from Google Maps · 30 in total saved.");
    await screen.findByText("Showing 1–12 of 30");
  });

  it("the partial banner says how many reviews were added and how many were skipped", async () => {
    backendHas(
      ingestionRun({
        target_id: TARGET_ID,
        status: "PARTIAL",
        reviews_ingested: 14,
        reviews_rejected: 3,
      }),
    );
    backendHoldsReviews(14);

    await openWorkspace("Partially ingested");

    await screen.findByText(
      "14 reviews added from Google Maps, 3 skipped as unusable · 14 in total saved.",
    );
  });

  it("a complete run shows no partial banner", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, reviews_ingested: 3, reviews_duplicate: 2 }));
    backendHoldsReviews(8);

    await openWorkspace("Ingested");

    await screen.findByText("3 reviews added from Google Maps · 8 in total saved.");
    expect(screen.queryByText(/skipped as unusable/)).toBeNull();
  });

  it("the partial banner can be dismissed", async () => {
    backendHas(
      ingestionRun({
        target_id: TARGET_ID,
        status: "PARTIAL",
        reviews_ingested: 14,
        reviews_rejected: 3,
      }),
    );
    backendHoldsReviews(14);
    await openWorkspace("Partially ingested");
    await screen.findByText(/3 skipped as unusable/);

    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(screen.queryByText(/3 skipped as unusable/)).toBeNull();
  });

  // S2-007 / S2-BR-006: skipped records say why, and that they are not in the dataset.
  const partialRun = () =>
    ingestionRun({
      target_id: TARGET_ID,
      status: "PARTIAL",
      reviews_ingested: 14,
      reviews_rejected: 4,
    });

  it("the partial banner says why records were skipped and that they are not in the dataset", async () => {
    backendHas(partialRun(), [
      { reason: "MISSING_TEXT", count: 3 },
      { reason: "MISSING_RATING", count: 1 },
    ]);
    backendHoldsReviews(14);

    await openWorkspace("Partially ingested");

    await screen.findByText(
      "Skipped: no written review (star rating only) ×3, no star rating ×1. " +
        "These records were not added to your reviews.",
    );
  });

  it("shows an unknown skip reason as 'other reason', never as raw text", async () => {
    backendHas(partialRun(), [{ reason: "SOMETHING_NEW" as never, count: 4 }]);
    backendHoldsReviews(14);

    await openWorkspace("Partially ingested");

    await screen.findByText(/Skipped: other reason ×4\./);
    expect(screen.queryByText(/SOMETHING_NEW/)).toBeNull();
  });

  it("shows no 'Skipped:' line when the run skipped nothing", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, reviews_ingested: 3 }));
    backendHoldsReviews(3);

    await openWorkspace("Ingested");

    expect(screen.queryByText(/Skipped:/)).toBeNull();
  });

  it("the Ingestion tab lists why records were skipped", async () => {
    backendHas(partialRun(), [
      { reason: "MISSING_TEXT", count: 3 },
      { reason: "MISSING_RATING", count: 1 },
    ]);
    backendHoldsReviews(14);
    await openWorkspace("Partially ingested");

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Ingestion" }));

    await screen.findByText("Why records were skipped");
    expect(screen.getByText("No written review (star rating only)")).toBeTruthy();
    expect(screen.getByText("No star rating")).toBeTruthy();
    expect(screen.getByText("These records were not added to your reviews.")).toBeTruthy();
    // A table with named columns, and a total that adds the reasons up (3 + 1).
    expect(screen.getByRole("columnheader", { name: "Reason" })).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Records" })).toBeTruthy();
    const total = screen.getByText("Total skipped").closest("tr");
    expect(total?.textContent).toBe("Total skipped4");
  });

  it("the Ingestion tab shows no total row when there is only one reason", async () => {
    backendHas(partialRun(), [{ reason: "MISSING_TEXT", count: 4 }]);
    backendHoldsReviews(14);
    await openWorkspace("Partially ingested");

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Ingestion" }));

    await screen.findByText("Why records were skipped");
    expect(screen.getByText("No written review (star rating only)")).toBeTruthy();
    expect(screen.queryByText("Total skipped")).toBeNull();
  });

  it("the Ingestion tab has no skip list when nothing was skipped", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, reviews_ingested: 3 }));
    backendHoldsReviews(3);
    await openWorkspace("Ingested");

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Ingestion" }));

    await screen.findByText("Latest run");
    expect(screen.queryByText("Why records were skipped")).toBeNull();
  });

  it("shows Ingested for a completed run even when the target has no review rows", async () => {
    backendHas(ingestionRun({ target_id: TARGET_ID, status: "COMPLETED", reviews_ingested: 5 }));
    backendHoldsReviews(0);

    await openWorkspace("Ingested");

    expect(screen.queryByText("Failed")).toBeNull();
  });

  it("says reviews were found but none could be used when every record was skipped", async () => {
    backendHas(
      ingestionRun({
        target_id: TARGET_ID,
        status: "FAILED",
        error_code: "NO_REVIEWS_FOUND",
        reviews_rejected: 2,
      }),
      [{ reason: "MISSING_TEXT", count: 2 }],
    );
    backendHoldsReviews(0);

    await openWorkspace("Failed");

    await screen.findByText("Reviews were found, but none could be used.");
    expect(
      screen.getByText(
        "All 2 records were skipped: no written review (star rating only) ×2. " +
          "None of them were added to your reviews.",
      ),
    ).toBeTruthy();
    expect(screen.queryByText("This page didn't return any reviews.")).toBeNull();
  });

  it("still says the page returned nothing when no record was skipped", async () => {
    backendHas(
      ingestionRun({
        target_id: TARGET_ID,
        status: "FAILED",
        error_code: "NO_REVIEWS_FOUND",
        reviews_rejected: 0,
      }),
    );
    backendHoldsReviews(0);

    await openWorkspace("Failed");

    await screen.findByText("This page didn't return any reviews.");
  });

  it("shows Failed for a failed run even when the target has review rows", async () => {
    backendHas(failedRun);
    backendHoldsReviews(5);

    await openWorkspace("Failed");

    expect(screen.queryByText("Ingested")).toBeNull();
  });

  it("a failed page change keeps the page on screen and says so", async () => {
    backendHas(completedRun);
    backendHoldsReviews(30);
    await openWorkspace("Ingested");
    await screen.findByText("Showing 1–12 of 30");
    server.use(http.get(REVIEWS_URL, () => HttpResponse.error()));

    fireEvent.click(button("Next"));

    await screen.findByText("We couldn't load that page of reviews. Try again in a few minutes.");
    expect(screen.getByText("Showing 1–12 of 30")).toBeTruthy();
    expect(screen.getByText("Review number 0")).toBeTruthy();
    await waitFor(() => expect(button("Next").disabled).toBe(false));
  });
});
