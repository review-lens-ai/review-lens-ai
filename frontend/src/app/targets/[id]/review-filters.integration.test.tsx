// S3-016: filtering the Reviews list by rating and date. The whole workspace page is
// rendered and MSW plays the backend, which filters like the real endpoint: inclusive
// rating range, inclusive dates, AND, and total counting only the matches.
import { describe, expect, it, vi } from "vitest";
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
  within,
} from "@/test";

const TARGET_ID = "11111111-1111-1111-1111-111111111111";
const TARGET_URL = `${API}/api/v1/targets/${TARGET_ID}`;
const REVIEWS_URL = `${TARGET_URL}/reviews`;

const router = { replace: vi.fn() };
vi.mock("next/navigation", () => ({
  useRouter: () => router,
  useParams: () => ({ id: TARGET_ID }),
  useSearchParams: () => new URLSearchParams(""),
}));
vi.mock("@/lib/hooks/use-targets", () => ({
  useTargets: () => ({ refresh: vi.fn(async () => {}) }),
}));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import TargetWorkspacePage from "./page";

// 30 reviews: ratings 1,2,3,4,5,1,2,... and one a day from Jan 1, 2026 (newest first).
const DATASET = Array.from({ length: 30 }, (_, i) =>
  review({
    id: `review-${i}`,
    rating: (i % 5) + 1,
    review_text: `Review ${i}, ${(i % 5) + 1} stars`,
    reviewed_at: `2026-01-${String(i + 1).padStart(2, "0")}T12:00:00Z`,
  }),
).reverse();

// Sets up the page's endpoints. Returns every query string the reviews endpoint got.
function backendHoldsDataset() {
  const asked: URLSearchParams[] = [];
  server.use(
    http.get(TARGET_URL, () =>
      jsonResponse(target({ id: TARGET_ID, ingestion_status: "COMPLETED" })),
    ),
    http.get(`${TARGET_URL}/ingestion-runs/latest`, () =>
      jsonResponse({
        run: ingestionRun({ target_id: TARGET_ID, status: "COMPLETED" }),
        skipped_reasons: [],
      }),
    ),
    http.get(`${TARGET_URL}/summary`, () =>
      jsonResponse({
        review_count: 30,
        average_rating: 3,
        rating_counts: [1, 2, 3, 4, 5].map((rating) => ({ rating, count: 6 })),
      }),
    ),
    http.get(REVIEWS_URL, ({ request }) => {
      const query = new URL(request.url).searchParams;
      asked.push(query);
      const min = Number(query.get("min_rating") ?? 1);
      const max = Number(query.get("max_rating") ?? 5);
      const start = query.get("start_date");
      const end = query.get("end_date");
      const matching = DATASET.filter((r) => {
        const day = r.reviewed_at!.slice(0, 10);
        return (
          r.rating >= min && r.rating <= max && (!start || day >= start) && (!end || day <= end)
        );
      });
      const limit = Number(query.get("limit") ?? 50);
      const offset = Number(query.get("offset") ?? 0);
      return jsonResponse(
        reviewPage(matching.slice(offset, offset + limit), {
          total: matching.length,
          limit,
          offset,
        }),
      );
    }),
  );
  return asked;
}

async function openReviews() {
  render(<TargetWorkspacePage />, { wrapper: queryWrapper(() => router.replace("/login")) });
  await screen.findByRole("heading", { name: "Joe's Pizza" });
  await screen.findByText("Showing 1–12 of 30");
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
  });
}

function openFilters() {
  fireEvent.click(screen.getByRole("button", { name: "Filters" }));
  return screen.getByRole("form", { name: "Filter reviews" });
}

function choose(form: HTMLElement, label: string, value: string) {
  fireEvent.change(within(form).getByLabelText(label), { target: { value } });
}

function apply(form: HTMLElement) {
  fireEvent.click(within(form).getByRole("button", { name: "Apply filters" }));
}

function shownReviews(): string[] {
  return screen.queryAllByText(/^Review \d+, \d stars$/).map((el) => el.textContent ?? "");
}

describe("filtering the reviews list", () => {
  it("shows only reviews at or above the minimum rating, and says it is filtered", async () => {
    backendHoldsDataset();
    await openReviews();

    const form = openFilters();
    choose(form, "Minimum rating", "4");
    apply(form);

    await screen.findByText("Showing 1–12 of 12 matching (30 in all)");
    expect(shownReviews().every((text) => /, [45] stars$/.test(text))).toBe(true);
    expect(screen.getByText(/4 stars or more/)).toBeTruthy();
  });

  it("combines a rating range and a date range, sending the user's timezone", async () => {
    const asked = backendHoldsDataset();
    await openReviews();

    const form = openFilters();
    choose(form, "Minimum rating", "1");
    choose(form, "Maximum rating", "2");
    choose(form, "From date", "2026-01-01");
    choose(form, "To date", "2026-01-10");
    apply(form);

    // Jan 1-10 holds ratings 1,2,3,4,5,1,2,3,4,5: four of them are 1 or 2 stars.
    await screen.findByText("Showing 1–4 of 4 matching (30 in all)");
    const last = asked.at(-1)!;
    expect(last.get("min_rating")).toBe("1");
    expect(last.get("max_rating")).toBe("2");
    expect(last.get("start_date")).toBe("2026-01-01");
    expect(last.get("end_date")).toBe("2026-01-10");
    expect(last.get("timezone")).toBe(Intl.DateTimeFormat().resolvedOptions().timeZone);
  });

  it("changes nothing until Apply, and Cancel keeps the current results", async () => {
    const asked = backendHoldsDataset();
    await openReviews();
    const requestsBefore = asked.length;

    const form = openFilters();
    choose(form, "Minimum rating", "5");
    fireEvent.click(within(form).getByRole("button", { name: "Cancel" }));

    expect(screen.queryByRole("form", { name: "Filter reviews" })).toBeNull();
    expect(screen.getByText("Showing 1–12 of 30")).toBeTruthy();
    expect(asked.slice(requestsBefore).some((q) => q.has("min_rating"))).toBe(false);
    // Reopening starts from what is applied (nothing), not the cancelled choice.
    const reopened = openFilters();
    expect((within(reopened).getByLabelText("Minimum rating") as HTMLSelectElement).value).toBe("");
  });

  it("Clear all goes back to the complete dataset", async () => {
    backendHoldsDataset();
    await openReviews();
    const form = openFilters();
    choose(form, "Maximum rating", "1");
    apply(form);
    await screen.findByText("Showing 1–6 of 6 matching (30 in all)");

    fireEvent.click(screen.getByRole("button", { name: "Clear all" }));

    await screen.findByText("Showing 1–12 of 30");
    expect(screen.queryByText(/Filtered:/)).toBeNull();
  });

  it("says no reviews match, not that there are no reviews", async () => {
    backendHoldsDataset();
    await openReviews();
    const form = openFilters();
    choose(form, "Minimum rating", "5");
    choose(form, "From date", "2026-03-01");
    apply(form);

    await screen.findByText(/No reviews match these filters\. All 30 reviews are still saved/);
    expect(screen.queryByText(/No reviews yet/)).toBeNull();
    // The filters stay visible, so they can be cleared.
    expect(screen.getByRole("button", { name: "Clear all" })).toBeTruthy();
  });

  it("pages through the matching reviews only", async () => {
    const asked = backendHoldsDataset();
    await openReviews();
    const form = openFilters();
    choose(form, "From date", "2026-01-06");
    apply(form);
    await screen.findByText("Showing 1–12 of 25 matching (30 in all)");

    fireEvent.click(screen.getByRole("button", { name: "Next" }));

    await screen.findByText("Showing 13–24 of 25 matching (30 in all)");
    const last = asked.at(-1)!;
    expect([last.get("offset"), last.get("start_date")]).toEqual(["12", "2026-01-06"]);
  });

  it("explains an impossible range and sends nothing", async () => {
    const asked = backendHoldsDataset();
    await openReviews();
    const requestsBefore = asked.length;
    const form = openFilters();
    choose(form, "Minimum rating", "4");
    choose(form, "Maximum rating", "2");
    apply(form);

    expect(await within(form).findByRole("alert")).toHaveProperty(
      "textContent",
      "Maximum rating can't be lower than minimum.",
    );
    await waitFor(() => expect(asked.length).toBe(requestsBefore));
    expect(screen.getByText("Showing 1–12 of 30")).toBeTruthy();
  });
});
