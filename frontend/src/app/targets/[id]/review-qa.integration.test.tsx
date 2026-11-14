// Integration tests for the Review Q&A panel (S2-009). The whole workspace page is
// rendered, and MSW plays the backend, including the questions endpoint.
import { describe, expect, it, vi } from "vitest";
import type { AnswerResponse } from "@/lib/questions";
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
} from "@/test";

const TARGET_ID = "22222222-2222-2222-2222-222222222222";
const TARGET_URL = `${API}/api/v1/targets/${TARGET_ID}`;
const QUESTIONS_URL = `${TARGET_URL}/questions`;

const router = { replace: vi.fn() };
// The target in the URL. A test changes it to move to another target, as the sidebar does.
const url = vi.hoisted(() => ({ id: "22222222-2222-2222-2222-222222222222" }));

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useParams: () => ({ id: url.id }),
  // No ?review= link in these tests.
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/hooks/use-targets", () => ({
  useTargets: () => ({ refresh: vi.fn(async () => {}) }),
}));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import TargetWorkspacePage from "./page";

const QUESTION = "What are the most common complaints?";
const FAILED = "ReviewLens couldn't answer this question";
const ANSWER = "The main friction points are comfort and connectivity.";

// A reply in the shared answer format (backend/app/schemas/qa.py).
function answer(overrides: Partial<AnswerResponse> = {}): AnswerResponse {
  return {
    status: "ANSWERED",
    answer: ANSWER,
    evidence: [],
    context: {
      target_id: TARGET_ID,
      target_name: "Joe's Pizza",
      platform: "google_maps",
      reviews_considered: 5,
      reviews_total: 5,
      is_subset: false,
    },
    ...overrides,
  };
}

function answered(overrides: Partial<AnswerResponse> = {}) {
  return () => jsonResponse(answer(overrides));
}

// The target and its reviews. reviewTotal 0 means the target was never ingested.
function backendHas(reviewTotal: number, id = TARGET_ID, name = "Joe's Pizza") {
  const targetUrl = `${API}/api/v1/targets/${id}`;
  const ingested = reviewTotal > 0;
  const run = ingested ? ingestionRun({ target_id: id, status: "COMPLETED" }) : null;
  server.use(
    http.get(targetUrl, () =>
      jsonResponse(target({ id, name, ingestion_status: ingested ? "COMPLETED" : null })),
    ),
    http.get(`${targetUrl}/ingestion-runs/latest`, () =>
      jsonResponse({ run, skipped_reasons: [] }),
    ),
    http.get(`${targetUrl}/reviews`, () =>
      jsonResponse(reviewPage([review({ target_id: id })], { total: reviewTotal })),
    ),
    // The Ingestion tab shows the review summary (S2-006).
    http.get(`${targetUrl}/summary`, () =>
      jsonResponse({
        review_count: reviewTotal,
        average_rating: null,
        rating_counts: [1, 2, 3, 4, 5].map((rating) => ({ rating, count: 0 })),
      }),
    ),
  );
}

// Sets what the backend answers to a question about a target; returns the questions it received.
function questionsAnswer(
  respond: () => Response | Promise<Response>,
  questionsUrl = QUESTIONS_URL,
) {
  const asked: unknown[] = [];
  server.use(
    http.post(questionsUrl, async ({ request }) => {
      asked.push(await request.json());
      return respond();
    }),
  );
  return asked;
}

async function openQaPanel(name = "Joe's Pizza") {
  url.id = TARGET_ID;
  // As in the app: a 401 from any request goes to /login (QueryProvider does this there).
  const page = render(<TargetWorkspacePage />, {
    wrapper: queryWrapper(() => router.replace("/login")),
  });
  await screen.findByRole("heading", { name });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  fireEvent.click(qaButton());
  return page;
}

// The header button that opens and closes the Q&A panel.
function qaButton() {
  return screen.getByRole<HTMLButtonElement>("button", { name: "Ask about reviews" });
}

// The panel, named after the target it answers about (S2-BR-014).
function qaPanel(name = "Joe's Pizza") {
  return screen.getByRole("region", { name: `Ask ${name}` });
}

function questionBox() {
  return screen.getByRole<HTMLTextAreaElement>("textbox", { name: "Your question" });
}

function askButton(name = "Ask ReviewLens") {
  return screen.getByRole<HTMLButtonElement>("button", { name });
}

function typeQuestion(text: string) {
  fireEvent.change(questionBox(), { target: { value: text } });
}

// Types a question and asks it with the button.
function askQuestion(text = QUESTION) {
  typeQuestion(text);
  fireEvent.click(askButton());
}

function response() {
  return screen.findByRole("article", { name: "ReviewLens response" });
}

describe("Review Q&A panel", () => {
  it("shows the question box and suggestions for a target with reviews", async () => {
    backendHas(42);

    await openQaPanel();

    expect(qaButton().getAttribute("aria-expanded")).toBe("true");
    expect(questionBox().value).toBe("");
    // Opening the panel puts the cursor in the question box.
    expect(document.activeElement).toBe(questionBox());
    expect(screen.getByRole("button", { name: "What do customers love most?" })).toBeTruthy();
    // The dataset answers come from is named next to the question (S2-BR-014).
    await waitFor(() => expect(qaPanel().textContent).toContain("42 reviews · Google Maps"));
    // Each question stands alone: no earlier turns are sent.
    expect(qaPanel().textContent).toContain("Each question is answered on its own");
  });

  it("points to ingestion when the target has no reviews", async () => {
    backendHas(0);

    await openQaPanel();

    await screen.findByRole("heading", { name: "No reviews to ask about yet" });
    expect(screen.queryByRole("textbox", { name: "Your question" })).toBeNull();
  });

  it("can't ask until there is a question", async () => {
    backendHas(5);
    const asked = questionsAnswer(answered());
    await openQaPanel();

    expect(askButton().disabled).toBe(true);
    fireEvent.click(askButton());
    typeQuestion(QUESTION);

    expect(askButton().disabled).toBe(false);
    expect(asked).toEqual([]);
  });

  it("sends only the question and shows the answer after it", async () => {
    backendHas(5);
    const asked = questionsAnswer(answered());
    await openQaPanel();

    askQuestion();

    const reply = await response();
    expect(reply.textContent).toContain(ANSWER);
    expect(reply.textContent).toContain("Answered");
    // The question shows in the thread, read out as "You asked: …".
    expect(screen.getByText((_, el) => el?.textContent === `You asked: ${QUESTION}`)).toBeTruthy();
    expect(asked).toEqual([{ question: QUESTION }]);
    // The box is ready for the next question.
    expect(questionBox().value).toBe("");
  });

  it("keeps earlier answers above a new one, and still sends only the new question", async () => {
    backendHas(5);
    const asked = questionsAnswer(answered());
    await openQaPanel();
    askQuestion();
    await response();

    questionsAnswer(answered({ answer: "Service is the top praise." }));
    askQuestion("What do customers love most?");

    await screen.findByText("Service is the top praise.");
    expect(screen.getAllByRole("article", { name: "ReviewLens response" })).toHaveLength(2);
    expect(asked).toEqual([{ question: QUESTION }]);
  });

  it("a suggestion asks that question", async () => {
    backendHas(5);
    const asked = questionsAnswer(answered());
    await openQaPanel();

    fireEvent.click(screen.getByRole("button", { name: "What do reviewers mention most often?" }));

    await response();
    expect(asked).toEqual([{ question: "What do reviewers mention most often?" }]);
  });

  it("lists the reviews behind an answer, and says when only some were read", async () => {
    backendHas(120);
    questionsAnswer(
      answered({
        evidence: [
          { id: "e1", rating: 1, review_text: "Waited an hour.", reviewed_at: null },
          { id: "e2", rating: 2, review_text: "Slow at lunch.", reviewed_at: null },
        ],
        context: {
          ...answer().context,
          reviews_considered: 40,
          reviews_total: 120,
          is_subset: true,
        },
      }),
    );
    await openQaPanel();

    askQuestion();

    const reply = await response();
    expect(reply.textContent).toContain("Based on 40 of 120 reviews");
    expect(reply.textContent).toContain("Evidence: 2 reviews");
    expect(reply.textContent).toContain("Waited an hour.");
    expect(screen.getAllByRole("img", { name: "1 out of 5 stars" })).toHaveLength(1);
  });

  // S2-BR-021: a reply that isn't an answer must not look like one.
  it.each([
    ["INSUFFICIENT_EVIDENCE", "Not enough evidence"],
    ["OUT_OF_SCOPE", "Outside these reviews"],
    ["NO_REVIEWS", "No reviews yet"],
  ] as const)("labels a %s reply as %s", async (status, label) => {
    backendHas(5);
    questionsAnswer(answered({ status, answer: "I can't answer that from these reviews." }));
    await openQaPanel();

    askQuestion();

    const reply = await response();
    expect(reply.textContent).toContain(label);
    expect(reply.textContent).not.toContain("Answered");
    expect(reply.textContent).not.toContain("Based on");
  });

  it("shows it is working and blocks a second ask until the answer arrives", async () => {
    backendHas(5);
    let finish = () => {};
    const asked = questionsAnswer(async () => {
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
      return jsonResponse(answer());
    });
    await openQaPanel();

    askQuestion();

    await screen.findByRole("status", { name: "ReviewLens is reading the reviews" });
    typeQuestion("Another question");
    expect(askButton("Analyzing…").disabled).toBe(true);
    fireEvent.submit(questionBox().form!);
    await act(async () => finish());
    await response();
    expect(asked).toHaveLength(1);
  });

  it("a failed request explains it and can try again", async () => {
    backendHas(5);
    questionsAnswer(() => apiErrorResponse(504, "TIMEOUT", "Gateway Timeout"));
    await openQaPanel();

    askQuestion();

    const alert = await screen.findByRole("alert", { name: FAILED });
    expect(alert.textContent).toContain("The AI service didn't respond in time.");
    expect(alert.textContent).not.toContain("Gateway Timeout");

    const asked = questionsAnswer(answered());
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await response();
    expect(asked).toEqual([{ question: QUESTION }]);
    expect(screen.queryByRole("alert", { name: FAILED })).toBeNull();
  });

  it("a failed question can be put back in the box to change it", async () => {
    backendHas(5);
    questionsAnswer(() => apiErrorResponse(504, "TIMEOUT", "Gateway Timeout"));
    await openQaPanel();
    askQuestion();
    await screen.findByRole("alert", { name: FAILED });

    fireEvent.click(screen.getByRole("button", { name: "Edit your question" }));

    await waitFor(() => expect(questionBox().value).toBe(QUESTION));
  });

  it("a network failure says so without technical details", async () => {
    backendHas(5);
    questionsAnswer(() => HttpResponse.error());
    await openQaPanel();

    askQuestion();

    const alert = await screen.findByRole("alert", { name: FAILED });
    expect(alert.textContent).toContain("We couldn't reach ReviewLens. Check your connection.");
  });

  it("a 401 sends the user to sign in", async () => {
    backendHas(5);
    questionsAnswer(() => apiErrorResponse(401, "UNAUTHORIZED", "Sign in."));
    await openQaPanel();

    askQuestion();

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  });

  it("a 404 shows the not found page", async () => {
    backendHas(5);
    questionsAnswer(() => apiErrorResponse(404, "NOT_FOUND", "Not found."));
    await openQaPanel();

    askQuestion();

    await screen.findByRole("heading", { name: "Target not found" });
  });

  it("a 404 without the NOT_FOUND code is a failed question, not a missing target", async () => {
    backendHas(5);
    questionsAnswer(() => jsonResponse({ detail: "Not Found" }, 404));
    await openQaPanel();

    askQuestion();

    await screen.findByRole("alert", { name: FAILED });
    expect(screen.queryByRole("heading", { name: "Target not found" })).toBeNull();
  });

  it("is closed until the button opens it", async () => {
    backendHas(5);
    url.id = TARGET_ID;
    render(<TargetWorkspacePage />, { wrapper: queryWrapper() });
    await screen.findByRole("heading", { name: "Joe's Pizza" });

    expect(qaButton().getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByRole("region", { name: "Ask Joe's Pizza" })).toBeNull();
    // The tabs are in the header while the panel is closed.
    expect(screen.getAllByRole("tab").map((tab) => tab.textContent)).toEqual([
      "Ingestion",
      "Reviews",
    ]);
  });

  it("keeps the tabs usable beside the open panel", async () => {
    backendHas(5);
    await openQaPanel();

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Ingestion" }));

    await screen.findByRole("heading", { name: "Latest run" });
    expect(qaPanel()).toBeTruthy();
  });

  it("✕ closes the panel and returns focus to the button", async () => {
    backendHas(5);
    await openQaPanel();

    fireEvent.click(screen.getByRole("button", { name: "Close Q&A" }));

    expect(screen.queryByRole("region", { name: "Ask Joe's Pizza" })).toBeNull();
    expect(qaButton().getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(qaButton());
  });

  it("Esc closes the panel", async () => {
    backendHas(5);
    await openQaPanel();

    fireEvent.keyDown(questionBox(), { key: "Escape" });

    expect(screen.queryByRole("region", { name: "Ask Joe's Pizza" })).toBeNull();
  });

  it("keeps the conversation when the panel is closed and opened again", async () => {
    backendHas(5);
    questionsAnswer(answered());
    await openQaPanel();
    askQuestion();
    await response();

    fireEvent.click(qaButton());
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Ingestion" }));
    fireEvent.click(qaButton());

    expect((await response()).textContent).toContain(ANSWER);
  });
});

// S2-BR-014: Q&A stays with the selected target. Tested across a switch, not each target alone.
describe("Review Q&A when the user switches targets", () => {
  const OTHER_ID = "33333333-3333-3333-3333-333333333333";
  const OTHER_NAME = "Mia's Tacos";
  const OTHER_QUESTIONS_URL = `${API}/api/v1/targets/${OTHER_ID}/questions`;

  // Moves to a target, as clicking it in the sidebar does. The Q&A panel stays open.
  async function switchTo(page: ReturnType<typeof render>, id: string, name: string) {
    url.id = id;
    page.rerender(<TargetWorkspacePage />);
    await screen.findByRole("heading", { name });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it("the new target starts with no earlier answer and its own name", async () => {
    backendHas(5);
    backendHas(7, OTHER_ID, OTHER_NAME);
    questionsAnswer(answered());
    const page = await openQaPanel();
    askQuestion();
    await response();

    await switchTo(page, OTHER_ID, OTHER_NAME);

    expect(questionBox().value).toBe("");
    expect(screen.queryByRole("article", { name: "ReviewLens response" })).toBeNull();
    expect(screen.queryByText(ANSWER)).toBeNull();
    // The panel stays open, now about the new target.
    await waitFor(() => expect(qaPanel(OTHER_NAME).textContent).toContain("7 reviews"));
    expect(screen.queryByRole("region", { name: "Ask Joe's Pizza" })).toBeNull();
  });

  it("a half-typed question doesn't follow the user to the new target", async () => {
    backendHas(5);
    backendHas(7, OTHER_ID, OTHER_NAME);
    const page = await openQaPanel();
    typeQuestion(QUESTION);

    await switchTo(page, OTHER_ID, OTHER_NAME);

    expect(questionBox().value).toBe("");
  });

  it("questions after the switch go to the new target only", async () => {
    backendHas(5);
    backendHas(7, OTHER_ID, OTHER_NAME);
    const askedFirst = questionsAnswer(answered());
    const askedOther = questionsAnswer(answered({ answer: "Tacos answer." }), OTHER_QUESTIONS_URL);
    const page = await openQaPanel();
    await switchTo(page, OTHER_ID, OTHER_NAME);

    askQuestion();

    expect((await response()).textContent).toContain("Tacos answer.");
    expect(askedOther).toEqual([{ question: QUESTION }]);
    expect(askedFirst).toEqual([]);
  });

  // Going back to a target already loaded shows it at once, with no loading state in
  // between, so nothing but the per-target reset keeps the last target's answer off it.
  it("going back to an already loaded target does not carry the answer over", async () => {
    backendHas(5);
    backendHas(7, OTHER_ID, OTHER_NAME);
    questionsAnswer(answered({ answer: "Tacos answer." }), OTHER_QUESTIONS_URL);
    const page = await openQaPanel();
    await switchTo(page, OTHER_ID, OTHER_NAME);
    askQuestion();
    await response();

    await switchTo(page, TARGET_ID, "Joe's Pizza");

    expect(screen.queryByText("Tacos answer.")).toBeNull();
    await waitFor(() => expect(qaPanel().textContent).toContain("5 reviews"));
    expect(screen.queryByRole("region", { name: `Ask ${OTHER_NAME}` })).toBeNull();
  });

  it("a late answer for the previous target never shows on the new one", async () => {
    backendHas(5);
    backendHas(7, OTHER_ID, OTHER_NAME);
    let finish = () => {};
    questionsAnswer(async () => {
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
      return jsonResponse(answer());
    });
    const page = await openQaPanel();
    askQuestion();
    await screen.findByRole("status", { name: "ReviewLens is reading the reviews" });

    await switchTo(page, OTHER_ID, OTHER_NAME);
    await act(async () => {
      finish();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.queryByText(ANSWER)).toBeNull();
    expect(screen.queryByRole("status", { name: "ReviewLens is reading the reviews" })).toBeNull();
    typeQuestion(QUESTION);
    expect(askButton().disabled).toBe(false);
  });
});
