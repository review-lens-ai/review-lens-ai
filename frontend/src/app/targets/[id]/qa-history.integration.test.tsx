// Integration tests for saved Q&A history: restoring it when a target is reopened
// (S3-007) and clearing it (S3-008). The whole workspace page is rendered, and MSW plays
// the backend, including the history endpoints agreed for S3-006 (src/lib/qa-history.ts).
import { focusManager } from "@tanstack/react-query";
import { delay } from "msw";
import { describe, expect, it, vi } from "vitest";
import type { AnswerResponse } from "@/lib/generated/api";
import { dayLabel, type SavedQaExchange, timeLabel } from "@/lib/qa-history";
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

const PIZZA_ID = "33333333-3333-3333-3333-333333333333";
const DINER_ID = "44444444-4444-4444-4444-444444444444";

const router = { replace: vi.fn() };
// The target in the URL. A test changes it to move to another target, as the sidebar does.
const url = vi.hoisted(() => ({ id: "33333333-3333-3333-3333-333333333333" }));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useParams: () => ({ id: url.id }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/hooks/use-targets", () => ({
  useTargets: () => ({ refresh: vi.fn(async () => {}) }),
}));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import TargetWorkspacePage from "./page";

// --- Test data ---

function answer(targetId: string, text: string): AnswerResponse {
  return {
    status: "ANSWERED",
    answer: text,
    evidence: [],
    context: {
      target_id: targetId,
      target_name: "",
      platform: "google_maps",
      reviews_considered: 5,
      reviews_total: 5,
      is_subset: false,
    },
  };
}

// Saved long ago, so the day heading is a full date wherever and whenever the tests run.
const CRUST_AT = new Date(2025, 2, 2, 14, 31).toISOString();
const SEATING_AT = new Date(2025, 2, 2, 14, 34).toISOString();

const PIZZA_HISTORY: SavedQaExchange[] = [
  {
    id: "qa-crust",
    question: "What do reviewers love about the pizza crust?",
    created_at: CRUST_AT,
    response: answer(PIZZA_ID, "Reviewers praise the fermented dough."),
  },
  {
    id: "qa-seating",
    question: "What do customers say about seating?",
    created_at: SEATING_AT,
    response: answer(PIZZA_ID, "Limited seating comes up in the reviews."),
  },
];

const DINER_HISTORY: SavedQaExchange[] = [
  {
    id: "qa-breakfast",
    question: "What do guests say about the breakfast menu?",
    created_at: new Date(2025, 2, 3, 10, 4).toISOString(),
    response: answer(DINER_ID, "Guests praise the fluffy pancakes."),
  },
];

// --- Helpers ---

// A target with `reviewTotal` saved reviews and the given saved Q&A history.
// Returns how many times its history was requested.
function backendHas(
  id: string,
  name: string,
  history: SavedQaExchange[] | (() => Response | Promise<Response>),
  reviewTotal = 631,
) {
  const targetUrl = `${API}/api/v1/targets/${id}`;
  const historyRequests = vi.fn(
    typeof history === "function" ? history : () => jsonResponse({ items: history }),
  );
  server.use(
    http.get(targetUrl, () => jsonResponse(target({ id, name, ingestion_status: "COMPLETED" }))),
    http.get(`${targetUrl}/ingestion-runs/latest`, () =>
      jsonResponse({
        run: ingestionRun({ target_id: id, status: "COMPLETED" }),
        skipped_reasons: [],
      }),
    ),
    http.get(`${targetUrl}/reviews`, () =>
      jsonResponse(reviewPage([review({ target_id: id })], { total: reviewTotal })),
    ),
    http.get(`${targetUrl}/summary`, () =>
      jsonResponse({
        review_count: reviewTotal,
        average_rating: null,
        rating_counts: [1, 2, 3, 4, 5].map((rating) => ({ rating, count: 0 })),
      }),
    ),
    http.get(`${targetUrl}/questions`, historyRequests),
  );
  return historyRequests;
}

// Sets what clearing a target's history answers; returns the mock, to count the calls.
function clearingReturns(id: string, respond: () => Response) {
  const clear = vi.fn(respond);
  server.use(http.delete(`${API}/api/v1/targets/${id}/questions`, clear));
  return clear;
}

async function openWorkspace(name: string) {
  render(<TargetWorkspacePage />, { wrapper: queryWrapper(() => router.replace("/login")) });
  await screen.findByRole("heading", { name });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

async function openQaPanel(name = "L'industrie Pizzeria") {
  url.id = PIZZA_ID;
  const page = render(<TargetWorkspacePage />, {
    wrapper: queryWrapper(() => router.replace("/login")),
  });
  await screen.findByRole("heading", { name });
  fireEvent.click(screen.getByRole("button", { name: "Ask about reviews" }));
  await waitFor(() =>
    expect(screen.queryByRole("status", { name: "Loading your questions" })).toBeNull(),
  );
  return page;
}

function qaPanel(name = "L'industrie Pizzeria") {
  return screen.getByRole("region", { name: `Ask ${name}` });
}

function clearButton() {
  return within(qaPanel()).getByRole<HTMLButtonElement>("button", {
    name: "Clear Q&A History",
  });
}

// The questions on screen, in order.
function askedQuestions(): string[] {
  return screen
    .queryAllByText((_, el) => el?.tagName === "P" && !!el.textContent?.startsWith("You asked: "))
    .map((el) => el.textContent!.replace("You asked: ", ""));
}

// --- S3-007 ---

describe("restoring saved Q&A history", () => {
  it("shows the saved questions and answers in order, marked as restored", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);

    await openQaPanel();

    expect(askedQuestions()).toEqual([
      "What do reviewers love about the pizza crust?",
      "What do customers say about seating?",
    ]);
    const panel = qaPanel();
    within(panel).getByText("Reviewers praise the fermented dough.");
    within(panel).getByText("Limited seating comes up in the reviews.");
    within(panel).getByText("History restored");
    expect(panel.textContent).toContain("2 questions · 631 reviews · Google Maps");
  });

  it("shows one day heading for questions asked the same day, and each question's time", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);

    await openQaPanel();

    const panel = qaPanel();
    expect(within(panel).getAllByText(dayLabel(CRUST_AT))).toHaveLength(1);
    within(panel).getByText(timeLabel(CRUST_AT));
    within(panel).getByText(timeLabel(SEATING_AT));
  });

  it("does not ask for the history until the panel is opened", async () => {
    url.id = PIZZA_ID;
    const historyRequests = backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);

    await openWorkspace("L'industrie Pizzeria");

    expect(historyRequests).not.toHaveBeenCalled();
  });

  it("switching targets shows only the new target's history", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);
    backendHas(DINER_ID, "Top's Diner", DINER_HISTORY, 412);
    const { rerender } = await openQaPanel();

    url.id = DINER_ID;
    rerender(<TargetWorkspacePage />);

    await screen.findByText("Guests praise the fluffy pancakes.");
    expect(askedQuestions()).toEqual(["What do guests say about the breakfast menu?"]);
    expect(screen.queryByText("Reviewers praise the fermented dough.")).toBeNull();
    expect(qaPanel("Top's Diner").textContent).toContain("1 question · 412 reviews");
  });

  it("a target with no saved questions starts with suggestions and no restored label", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", []);

    await openQaPanel();

    within(qaPanel()).getByRole("button", { name: "What do customers love most?" });
    expect(within(qaPanel()).queryByText("History restored")).toBeNull();
  });

  it("if the history can't load, says so and still lets the user ask", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", () =>
      apiErrorResponse(500, "INTERNAL", "Something went wrong."),
    );

    await openQaPanel();

    within(qaPanel()).getByText(/earlier questions couldn't load/);
    within(qaPanel()).getByRole("textbox", { name: "Your question" });
  }, 10_000);

  // Regression: with the history endpoint not deployed (the server answers 405 to a GET on
  // the ask route), refreshing the history after an answer used to unmount the
  // conversation, so the answer vanished and the panel showed suggestions again.
  it("an answer stays on screen when the history endpoint is not available", async () => {
    // A real network takes a moment, so the page renders while the history is refetched.
    backendHas(PIZZA_ID, "L'industrie Pizzeria", async () => {
      await delay(30);
      return jsonResponse({ detail: "Method Not Allowed" }, 405);
    });
    server.use(
      http.post(`${API}/api/v1/targets/${PIZZA_ID}/questions`, () =>
        jsonResponse(answer(PIZZA_ID, "Service is quick at lunch.")),
      ),
    );
    await openQaPanel();

    fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), {
      target: { value: "How fast is service?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask ReviewLens" }));

    await screen.findByText("Service is quick at lunch.");
    // Let the history refresh after the answer finish.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    screen.getByText("Service is quick at lunch.");
    expect(askedQuestions()).toEqual(["How fast is service?"]);

    // Switching browser tabs and back refetches every failed query.
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    focusManager.setFocused(undefined);
    expect(askedQuestions()).toEqual(["How fast is service?"]);
    screen.getByText("Service is quick at lunch.");
  });

  it("another user's target (404) shows the not found page", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", () =>
      apiErrorResponse(404, "NOT_FOUND", "Target not found."),
    );
    url.id = PIZZA_ID;
    render(<TargetWorkspacePage />, { wrapper: queryWrapper() });
    await screen.findByRole("heading", { name: "L'industrie Pizzeria" });

    fireEvent.click(screen.getByRole("button", { name: "Ask about reviews" }));

    await screen.findByText("Target not found");
    expect(screen.queryByText("Reviewers praise the fermented dough.")).toBeNull();
  });

  it("a new answer is added after the restored ones and refreshes the saved history", async () => {
    const historyRequests = backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);
    server.use(
      http.post(`${API}/api/v1/targets/${PIZZA_ID}/questions`, () =>
        jsonResponse(answer(PIZZA_ID, "Service is quick at lunch.")),
      ),
    );
    await openQaPanel();
    expect(historyRequests).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), {
      target: { value: "How fast is service?" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Ask ReviewLens" }));

    await screen.findByText("Service is quick at lunch.");
    expect(askedQuestions()).toEqual([
      "What do reviewers love about the pizza crust?",
      "What do customers say about seating?",
      "How fast is service?",
    ]);
    await waitFor(() => expect(historyRequests).toHaveBeenCalledTimes(2));
  });
});

// --- S3-008 ---

describe("clearing Q&A history", () => {
  it("is off when there is nothing to clear", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", []);

    await openQaPanel();

    expect(clearButton().disabled).toBe(true);
  });

  it("sits next to Ask ReviewLens, separate from Delete Analysis", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);

    await openQaPanel();

    expect(clearButton().nextElementSibling?.textContent).toContain("Ask ReviewLens");
    // Delete Analysis stays in the workspace header, not in the Q&A panel.
    expect(within(qaPanel()).queryByRole("button", { name: "Delete Analysis" })).toBeNull();
  });

  it("the dialog says how many questions go and that the analysis and reviews stay", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);
    await openQaPanel();

    fireEvent.click(clearButton());

    const dialog = await screen.findByRole("alertdialog");
    within(dialog).getByRole("heading", { name: "Clear Q&A history?" });
    within(dialog).getByText(/remove the 2 saved questions and answers for L'industrie Pizzeria/);
    expect(
      within(dialog)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      "Analysis: L'industrie Pizzeria",
      "Google Maps source URL",
      "All 631 persisted reviews",
      "Ingestion summary",
    ]);
    within(dialog).getByText("Other analyses and their Q&A histories are not affected.");
  });

  it("confirming clears the conversation and keeps the reviews", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);
    const clear = clearingReturns(PIZZA_ID, () => new HttpResponse(null, { status: 204 }));
    await openQaPanel();
    fireEvent.click(clearButton());
    const dialog = await screen.findByRole("alertdialog");

    fireEvent.click(within(dialog).getByRole("button", { name: "Clear Q&A History" }));

    await screen.findByText("Q&A history cleared");
    expect(clear).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(askedQuestions()).toEqual([]);
    const panel = qaPanel();
    within(panel).getByText("A fresh start, same reviews.");
    within(panel).getByText("Ready for a new question");
    expect(panel.textContent).toContain("0 questions · 631 reviews");
    expect(clearButton().disabled).toBe(true);
    // The target and its reviews are still there.
    screen.getByRole("heading", { name: "L'industrie Pizzeria" });
    screen.getByText("Great slice, friendly staff.");
  });

  it("cancel keeps the history and sends nothing", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);
    const clear = clearingReturns(PIZZA_ID, () => new HttpResponse(null, { status: 204 }));
    await openQaPanel();
    fireEvent.click(clearButton());
    const dialog = await screen.findByRole("alertdialog");

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
    expect(clear).not.toHaveBeenCalled();
    expect(askedQuestions()).toHaveLength(2);
  });

  it("a failed clear keeps the history and says why", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);
    clearingReturns(PIZZA_ID, () => apiErrorResponse(409, "CONFLICT", "Try again in a moment."));
    await openQaPanel();
    fireEvent.click(clearButton());
    const dialog = await screen.findByRole("alertdialog");

    fireEvent.click(within(dialog).getByRole("button", { name: "Clear Q&A History" }));

    expect((await within(dialog).findByRole("alert")).textContent).toBe("Try again in a moment.");
    expect(askedQuestions()).toHaveLength(2);
    expect(screen.queryByText("Q&A history cleared")).toBeNull();
  });

  it("clearing one target leaves another target's history", async () => {
    backendHas(PIZZA_ID, "L'industrie Pizzeria", PIZZA_HISTORY);
    backendHas(DINER_ID, "Top's Diner", DINER_HISTORY, 412);
    const clearPizza = clearingReturns(PIZZA_ID, () => new HttpResponse(null, { status: 204 }));
    const clearDiner = clearingReturns(DINER_ID, () => new HttpResponse(null, { status: 204 }));
    const { rerender } = await openQaPanel();
    fireEvent.click(clearButton());
    fireEvent.click(
      within(await screen.findByRole("alertdialog")).getByRole("button", {
        name: "Clear Q&A History",
      }),
    );
    await screen.findByText("Q&A history cleared");

    url.id = DINER_ID;
    rerender(<TargetWorkspacePage />);

    await screen.findByText("Guests praise the fluffy pancakes.");
    expect(clearPizza).toHaveBeenCalledTimes(1);
    expect(clearDiner).not.toHaveBeenCalled();
  });
});
