// Integration tests for what the Q&A panel shows under a refusal (S2-014, S2-015): which
// kind of refusal it was, and questions to ask instead. The whole workspace page is
// rendered, and MSW plays the backend. The panel's other behavior is in
// review-qa.integration.test.tsx.
import { describe, expect, it, vi } from "vitest";
import type { AnswerResponse, RefusalReason } from "@/lib/generated/api";
import { REFUSAL_REASON_TEXT } from "@/lib/refusal-reasons";
import {
  act,
  API,
  fireEvent,
  http,
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

const TARGET_ID = "33333333-3333-3333-3333-333333333333";
const TARGET_URL = `${API}/api/v1/targets/${TARGET_ID}`;

// One router object for every render, so the page's effects don't re-run.
const router = { replace: vi.fn() };

vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useParams: () => ({ id: TARGET_ID }),
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/lib/hooks/use-targets", () => ({
  useTargets: () => ({ refresh: vi.fn(async () => {}) }),
}));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import TargetWorkspacePage from "./page";

const FIRST_SUGGESTION = "What do reviewers like most?";
const SECOND_SUGGESTION = "What do reviewers say about the service?";

// A refusal in the shared answer format (backend/app/schemas/qa.py).
function refusal(overrides: Partial<AnswerResponse> = {}): AnswerResponse {
  return {
    status: "OUT_OF_SCOPE",
    answer: "I only have Google Maps reviews for Joe's Pizza.",
    evidence: [],
    context: {
      target_id: TARGET_ID,
      target_name: "Joe's Pizza",
      platform: "google_maps",
      reviews_considered: 5,
      reviews_total: 5,
      is_subset: false,
    },
    refusal_reason: "OTHER_PLATFORM",
    suggestions: [FIRST_SUGGESTION, SECOND_SUGGESTION],
    ...overrides,
  };
}

function backendHasFiveReviews() {
  server.use(
    http.get(TARGET_URL, () =>
      jsonResponse(target({ id: TARGET_ID, name: "Joe's Pizza", ingestion_status: "COMPLETED" })),
    ),
    http.get(`${TARGET_URL}/ingestion-runs/latest`, () =>
      jsonResponse({
        run: ingestionRun({ target_id: TARGET_ID, status: "COMPLETED" }),
        skipped_reasons: [],
      }),
    ),
    http.get(`${TARGET_URL}/reviews`, () =>
      jsonResponse(reviewPage([review({ target_id: TARGET_ID })], { total: 5 })),
    ),
    http.get(`${TARGET_URL}/summary`, () =>
      jsonResponse({
        review_count: 5,
        average_rating: null,
        rating_counts: [1, 2, 3, 4, 5].map((rating) => ({ rating, count: 0 })),
      }),
    ),
  );
}

// The backend answers every question with `reply`; returns the questions it received.
function questionsAnswer(reply: AnswerResponse) {
  const asked: { question: string }[] = [];
  server.use(
    http.post(`${TARGET_URL}/questions`, async ({ request }) => {
      asked.push((await request.json()) as { question: string });
      return jsonResponse(reply);
    }),
  );
  return asked;
}

async function askAbout(question: string) {
  render(<TargetWorkspacePage />, { wrapper: queryWrapper() });
  await screen.findByRole("heading", { name: "Joe's Pizza" });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask about reviews" }));
  fireEvent.change(screen.getByRole("textbox", { name: "Your question" }), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: "Ask ReviewLens" }));
  return screen.findByRole("article", { name: "ReviewLens response" });
}

describe("under a refusal", () => {
  it("says which kind of refusal it was and offers questions to ask instead", async () => {
    backendHasFiveReviews();
    questionsAnswer(refusal());

    const reply = await askAbout("What does Yelp say about it?");

    expect(reply.textContent).toContain(REFUSAL_REASON_TEXT.OTHER_PLATFORM);
    expect(reply.textContent).toContain("You could ask instead");
    expect(screen.getByRole("button", { name: FIRST_SUGGESTION })).toBeTruthy();
    expect(screen.getByRole("button", { name: SECOND_SUGGESTION })).toBeTruthy();
  });

  it.each(Object.entries(REFUSAL_REASON_TEXT))("shows the line for %s", async (reason, line) => {
    backendHasFiveReviews();
    questionsAnswer(refusal({ refusal_reason: reason as RefusalReason }));

    const reply = await askAbout("A question");

    expect(reply.textContent).toContain(line);
  });

  it("asks a suggested question when its chip is pressed", async () => {
    backendHasFiveReviews();
    const asked = questionsAnswer(refusal());
    await askAbout("What does Yelp say about it?");

    fireEvent.click(screen.getByRole("button", { name: FIRST_SUGGESTION }));

    await waitFor(() => expect(asked).toHaveLength(2));
    expect(asked[1]).toEqual({ question: FIRST_SUGGESTION });
  });

  it("shows no reason or chips under an answer", async () => {
    backendHasFiveReviews();
    questionsAnswer(refusal({ status: "ANSWERED", refusal_reason: null, suggestions: [] }));

    const reply = await askAbout("What do customers love most?");

    expect(reply.textContent).not.toContain("You could ask instead");
    expect(reply.textContent).not.toContain(REFUSAL_REASON_TEXT.OTHER_PLATFORM);
  });

  it("still shows a refusal that comes without a reason or suggestions", async () => {
    backendHasFiveReviews();
    questionsAnswer(refusal({ refusal_reason: undefined, suggestions: undefined }));

    const reply = await askAbout("What does Yelp say about it?");

    expect(reply.textContent).toContain("I only have Google Maps reviews for Joe's Pizza.");
    expect(reply.textContent).not.toContain("You could ask instead");
  });
});
