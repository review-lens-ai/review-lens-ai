// Integration tests for the Review Q&A tab (S2-009). The whole workspace page is
// rendered, and MSW plays the backend, including the questions endpoint.
import { describe, expect, it, vi } from "vitest";
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

async function openQaTab(name = "Joe's Pizza") {
  url.id = TARGET_ID;
  // As in the app: a 401 from any request goes to /login (QueryProvider does this there).
  const page = render(<TargetWorkspacePage />, {
    wrapper: queryWrapper(() => router.replace("/login")),
  });
  await screen.findByRole("heading", { name });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  // Radix tabs switch on mouse down, as a real click does.
  fireEvent.mouseDown(screen.getByRole("tab", { name: "Review Q&A" }));
  return page;
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

describe("Review Q&A tab", () => {
  it("shows the question box and suggestions for a target with reviews", async () => {
    backendHas(42);

    await openQaTab();

    expect(await screen.findByRole("heading", { name: "Ask your reviews" })).toBeTruthy();
    expect(questionBox().value).toBe("");
    expect(screen.getByRole("button", { name: "What do customers love most?" })).toBeTruthy();
    // The dataset answers come from is named next to the question (S2-BR-014).
    const context = screen.getByRole("complementary", { name: "Reviews used for answers" });
    await waitFor(() => expect(context.textContent).toContain("42 reviews · Google Maps"));
    expect(context.textContent).toContain("Joe's Pizza");
  });

  it("points to ingestion when the target has no reviews", async () => {
    backendHas(0);

    await openQaTab();

    await screen.findByRole("heading", { name: "No reviews to ask about yet" });
    expect(screen.queryByRole("textbox", { name: "Your question" })).toBeNull();
  });

  it("an empty question is not sent and says what to do", async () => {
    backendHas(5);
    const asked = questionsAnswer(() => jsonResponse({ answer: ANSWER }));
    await openQaTab();

    fireEvent.click(askButton());

    expect(questionBox().getAttribute("aria-invalid")).toBe("true");
    expect(screen.getAllByText("Enter a question to ask ReviewLens.").length).toBeGreaterThan(0);
    expect(asked).toEqual([]);
  });

  it("asks the question and shows the answer under it", async () => {
    backendHas(5);
    const asked = questionsAnswer(() => jsonResponse({ answer: ANSWER }));
    await openQaTab();

    typeQuestion(QUESTION);
    fireEvent.click(askButton());

    const response = await screen.findByRole("article", { name: "ReviewLens response" });
    expect(response.textContent).toContain(ANSWER);
    expect(response.textContent).toContain(`You asked: ${QUESTION}`);
    expect(asked).toEqual([{ question: QUESTION }]);
    // The question stays in the box for a follow-up edit.
    expect(questionBox().value).toBe(QUESTION);
  });

  it("a suggestion fills the box and asks it", async () => {
    backendHas(5);
    const asked = questionsAnswer(() => jsonResponse({ answer: ANSWER }));
    await openQaTab();

    fireEvent.click(screen.getByRole("button", { name: "What should we improve first?" }));

    await screen.findByRole("article", { name: "ReviewLens response" });
    expect(questionBox().value).toBe("What should we improve first?");
    expect(asked).toEqual([{ question: "What should we improve first?" }]);
  });

  it("shows it is working and blocks a second ask until the answer arrives", async () => {
    backendHas(5);
    let finish = () => {};
    const asked = questionsAnswer(async () => {
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
      return jsonResponse({ answer: ANSWER });
    });
    await openQaTab();
    typeQuestion(QUESTION);

    fireEvent.click(askButton());

    await screen.findByRole("status", { name: "ReviewLens is reading the reviews" });
    expect(askButton("Analyzing…").disabled).toBe(true);
    expect(questionBox().readOnly).toBe(true);
    fireEvent.submit(questionBox().form!);
    finish();
    await screen.findByRole("article", { name: "ReviewLens response" });
    expect(asked).toHaveLength(1);
  });

  it("a failed request explains it, keeps the question, and can try again", async () => {
    backendHas(5);
    questionsAnswer(() => apiErrorResponse(504, "TIMEOUT", "Gateway Timeout"));
    await openQaTab();
    typeQuestion(QUESTION);

    fireEvent.click(askButton());

    const alert = await screen.findByRole("alert", { name: FAILED });
    expect(alert.textContent).toContain("The AI service didn't respond in time.");
    expect(alert.textContent).not.toContain("Gateway Timeout");
    expect(questionBox().value).toBe(QUESTION);

    const asked = questionsAnswer(() => jsonResponse({ answer: ANSWER }));
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    await screen.findByRole("article", { name: "ReviewLens response" });
    expect(asked).toEqual([{ question: QUESTION }]);
    expect(screen.queryByRole("alert", { name: FAILED })).toBeNull();
  });

  it("a network failure says so without technical details", async () => {
    backendHas(5);
    questionsAnswer(() => HttpResponse.error());
    await openQaTab();
    typeQuestion(QUESTION);

    fireEvent.click(askButton());

    const alert = await screen.findByRole("alert", { name: FAILED });
    expect(alert.textContent).toContain("We couldn't reach ReviewLens. Check your connection.");
  });

  it("a 401 sends the user to sign in", async () => {
    backendHas(5);
    questionsAnswer(() => apiErrorResponse(401, "UNAUTHORIZED", "Sign in."));
    await openQaTab();
    typeQuestion(QUESTION);

    fireEvent.click(askButton());

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
  });

  it("a 404 shows the not found page", async () => {
    backendHas(5);
    questionsAnswer(() => apiErrorResponse(404, "NOT_FOUND", "Not found."));
    await openQaTab();
    typeQuestion(QUESTION);

    fireEvent.click(askButton());

    await screen.findByRole("heading", { name: "Target not found" });
  });

  it("a 404 without the NOT_FOUND code is a failed question, not a missing target", async () => {
    backendHas(5);
    questionsAnswer(() => jsonResponse({ detail: "Not Found" }, 404));
    await openQaTab();
    typeQuestion(QUESTION);

    fireEvent.click(askButton());

    await screen.findByRole("alert", { name: FAILED });
    expect(screen.queryByRole("heading", { name: "Target not found" })).toBeNull();
  });

  it("keeps the answer when the user looks at another tab and comes back", async () => {
    backendHas(5);
    questionsAnswer(() => jsonResponse({ answer: ANSWER }));
    await openQaTab();
    typeQuestion(QUESTION);
    fireEvent.click(askButton());
    await screen.findByRole("article", { name: "ReviewLens response" });

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Reviews" }));
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Review Q&A" }));

    expect(screen.getByRole("article", { name: "ReviewLens response" }).textContent).toContain(
      ANSWER,
    );
  });
});

// S2-BR-014: Q&A stays with the selected target. Tested across a switch, not each target alone.
describe("Review Q&A when the user switches targets", () => {
  const OTHER_ID = "33333333-3333-3333-3333-333333333333";
  const OTHER_NAME = "Mia's Tacos";
  const OTHER_QUESTIONS_URL = `${API}/api/v1/targets/${OTHER_ID}/questions`;

  // Moves to a target, as clicking it in the sidebar does, and opens its Q&A tab.
  async function switchTo(page: ReturnType<typeof render>, id: string, name: string) {
    url.id = id;
    page.rerender(<TargetWorkspacePage />);
    await screen.findByRole("heading", { name });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Review Q&A" }));
  }

  function contextCard() {
    return screen.getByRole("complementary", { name: "Reviews used for answers" });
  }

  it("the new target starts with an empty box, no earlier answer, and its own name", async () => {
    backendHas(5);
    backendHas(7, OTHER_ID, OTHER_NAME);
    questionsAnswer(() => jsonResponse({ answer: ANSWER }));
    const page = await openQaTab();
    typeQuestion(QUESTION);
    fireEvent.click(askButton());
    await screen.findByRole("article", { name: "ReviewLens response" });

    await switchTo(page, OTHER_ID, OTHER_NAME);

    expect(questionBox().value).toBe("");
    expect(screen.queryByRole("article", { name: "ReviewLens response" })).toBeNull();
    expect(screen.queryByText(ANSWER)).toBeNull();
    await waitFor(() => expect(contextCard().textContent).toContain("7 reviews"));
    expect(contextCard().textContent).toContain(OTHER_NAME);
    expect(contextCard().textContent).not.toContain("Joe's Pizza");
  });

  it("questions after the switch go to the new target only", async () => {
    backendHas(5);
    backendHas(7, OTHER_ID, OTHER_NAME);
    const askedFirst = questionsAnswer(() => jsonResponse({ answer: ANSWER }));
    const askedOther = questionsAnswer(
      () => jsonResponse({ answer: "Tacos answer." }),
      OTHER_QUESTIONS_URL,
    );
    const page = await openQaTab();
    await switchTo(page, OTHER_ID, OTHER_NAME);
    typeQuestion(QUESTION);

    fireEvent.click(askButton());

    const response = await screen.findByRole("article", { name: "ReviewLens response" });
    expect(response.textContent).toContain("Tacos answer.");
    expect(response.textContent).toContain(`Based on ${OTHER_NAME}'s reviews`);
    expect(askedOther).toEqual([{ question: QUESTION }]);
    expect(askedFirst).toEqual([]);
  });

  // Going back to a target already loaded shows it at once, with no loading state in
  // between, so nothing but the per-target reset keeps the last target's answer off it.
  it("going back to an already loaded target does not carry the answer over", async () => {
    backendHas(5);
    backendHas(7, OTHER_ID, OTHER_NAME);
    questionsAnswer(() => jsonResponse({ answer: "Tacos answer." }), OTHER_QUESTIONS_URL);
    const page = await openQaTab();
    await switchTo(page, OTHER_ID, OTHER_NAME);
    typeQuestion(QUESTION);
    fireEvent.click(askButton());
    await screen.findByRole("article", { name: "ReviewLens response" });

    await switchTo(page, TARGET_ID, "Joe's Pizza");

    expect(questionBox().value).toBe("");
    expect(screen.queryByText("Tacos answer.")).toBeNull();
    await waitFor(() => expect(contextCard().textContent).toContain("5 reviews"));
    expect(contextCard().textContent).not.toContain(OTHER_NAME);
  });

  it("a late answer for the previous target never shows on the new one", async () => {
    backendHas(5);
    backendHas(7, OTHER_ID, OTHER_NAME);
    let finish = () => {};
    questionsAnswer(async () => {
      await new Promise<void>((resolve) => {
        finish = resolve;
      });
      return jsonResponse({ answer: ANSWER });
    });
    const page = await openQaTab();
    typeQuestion(QUESTION);
    fireEvent.click(askButton());
    await screen.findByRole("status", { name: "ReviewLens is reading the reviews" });

    await switchTo(page, OTHER_ID, OTHER_NAME);
    await act(async () => {
      finish();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.queryByText(ANSWER)).toBeNull();
    expect(screen.queryByRole("status", { name: "ReviewLens is reading the reviews" })).toBeNull();
    expect(askButton().disabled).toBe(false);
  });
});
