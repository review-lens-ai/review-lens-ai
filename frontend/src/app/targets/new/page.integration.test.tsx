// Integration tests for the create-target form. The whole page is
// rendered, and MSW plays the backend.
import { describe, expect, it, vi } from "vitest";
import {
  API,
  apiErrorResponse,
  fireEvent,
  http,
  HttpResponse,
  jsonResponse,
  queryWrapper,
  render,
  screen,
  server,
  target,
  waitFor,
} from "@/test";

const TARGETS_URL = `${API}/api/v1/targets`;
const PLACE_URL = "https://www.google.com/maps/place/Joes+Pizza/data=!4m2!3m1!1s0x1:0x2";

// --- Replace the parts of the app this page uses but these tests don't cover ---

const router = { push: vi.fn(), replace: vi.fn() };
const refreshSidebar = vi.fn(async () => {});
const showToast = vi.fn();

vi.mock("next/navigation", () => ({ useRouter: () => router }));
vi.mock("@/lib/hooks/use-targets", () => ({
  useTargets: () => ({ refresh: refreshSidebar }),
}));
vi.mock("sonner", () => ({ toast: { success: (message: string) => showToast(message) } }));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import NewTargetPage from "./page";

// --- Helpers ---

function openForm() {
  // As in the app: a 401 from any request goes to /login (QueryProvider does this there).
  render(<NewTargetPage />, { wrapper: queryWrapper(() => router.replace("/login")) });
}

function nameInput() {
  return screen.getByLabelText<HTMLInputElement>(/Target name/);
}

function sourceUrlInput() {
  return screen.getByLabelText<HTMLInputElement>(/Source URL/);
}

function fillAndSubmit(name = "Joe's Pizza", sourceUrl = PLACE_URL) {
  fireEvent.change(nameInput(), { target: { value: name } });
  fireEvent.change(sourceUrlInput(), { target: { value: sourceUrl } });
  fireEvent.click(screen.getByRole("button", { name: "Create target" }));
}

// The backend's 422: { error: { code, message, details: [{ field, message }] } }.
function validationError(details: { field: string; message: string }[]) {
  return jsonResponse(
    { error: { code: "VALIDATION_ERROR", message: "Please fix the highlighted fields.", details } },
    422,
  );
}

const BAD_URL = "That doesn't look like a Google Maps link.";

// --- Tests ---

describe("creating a target", () => {
  it("sends the name and link, refreshes the sidebar, says so, then opens the target", async () => {
    const sent = vi.fn();
    server.use(
      http.post(TARGETS_URL, async ({ request }) => {
        sent(await request.json());
        return jsonResponse(target({ id: "new-1" }), 201);
      }),
    );
    openForm();

    fillAndSubmit();

    await waitFor(() => expect(router.push).toHaveBeenCalledWith("/targets/new-1"));
    expect(sent).toHaveBeenCalledWith({ name: "Joe's Pizza", source_url: PLACE_URL });
    expect(refreshSidebar).toHaveBeenCalled();
    expect(showToast).toHaveBeenCalledWith("Target created.");
  });

  it("shows Creating… and locks the form until the request finishes", async () => {
    let finish = () => {};
    server.use(
      http.post(TARGETS_URL, async () => {
        await new Promise<void>((resolve) => {
          finish = resolve;
        });
        return jsonResponse(target({ id: "new-1" }), 201);
      }),
    );
    openForm();

    fillAndSubmit();

    const button = await screen.findByRole<HTMLButtonElement>("button", { name: "Creating…" });
    expect(button.disabled).toBe(true);
    expect(nameInput().disabled).toBe(true);
    finish();
    await waitFor(() => expect(router.push).toHaveBeenCalled());
  });
});

describe("when the backend refuses", () => {
  it("a 422 shows each message under its field and focuses the first invalid one", async () => {
    server.use(
      http.post(TARGETS_URL, () => validationError([{ field: "source_url", message: BAD_URL }])),
    );
    openForm();

    fillAndSubmit();

    await screen.findByText(BAD_URL);
    expect(screen.getByText("Fix 1 field to continue.")).toBeTruthy();
    expect(document.activeElement).toBe(sourceUrlInput());
    expect(router.push).not.toHaveBeenCalled();
    // The user's input is kept (§6.2).
    expect(nameInput().value).toBe("Joe's Pizza");
  });

  it("after a 422, typing in another field keeps focus there", async () => {
    server.use(
      http.post(TARGETS_URL, () => validationError([{ field: "source_url", message: BAD_URL }])),
    );
    openForm();
    fillAndSubmit();
    await screen.findByText(BAD_URL);

    nameInput().focus();
    fireEvent.change(nameInput(), { target: { value: "Joe's Pizza, Newark" } });

    expect(document.activeElement).toBe(nameInput());
  });

  it("a network failure shows one message for the form and allows another try", async () => {
    server.use(http.post(TARGETS_URL, () => HttpResponse.error()));
    openForm();

    fillAndSubmit();

    await screen.findByText("Could not reach the server. Please try again.");
    await waitFor(() =>
      expect(
        screen.getByRole<HTMLButtonElement>("button", { name: "Create target" }).disabled,
      ).toBe(false),
    );
  });

  it("a 401 goes to sign in and shows no error", async () => {
    server.use(
      http.post(TARGETS_URL, () =>
        apiErrorResponse(401, "AUTHENTICATION_REQUIRED", "Please sign in to continue."),
      ),
    );
    openForm();

    fillAndSubmit();

    await waitFor(() => expect(router.replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("Please sign in to continue.")).toBeNull();
  });
});
