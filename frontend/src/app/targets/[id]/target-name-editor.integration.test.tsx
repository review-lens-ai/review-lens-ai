// S3-002: renaming a target from the workspace header. MSW plays the backend. The
// harness loads the target with useGetTarget, as the page does, so the test also
// checks that the header shows the saved name.
import { describe, expect, it, vi } from "vitest";
import {
  API,
  apiErrorResponse,
  fireEvent,
  http,
  jsonResponse,
  queryWrapper,
  render,
  screen,
  server,
  target,
  waitFor,
} from "@/test";

const showToast = vi.fn();
vi.mock("sonner", () => ({ toast: { success: (message: string) => showToast(message) } }));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import { useGetTarget } from "@/lib/generated/api";
import { TargetNameEditor } from "./_components/target-name-editor";

const ID = "target-1";
const TARGET_URL = `${API}/api/v1/targets/${ID}`;
const LIST_URL = `${API}/api/v1/targets`;
const OLD = target({ id: ID, name: "Joe's Pizza" });

function Harness() {
  const { data } = useGetTarget(ID);
  return data ? <TargetNameEditor target={data} /> : null;
}

// The backend: GET the target and the list (counted), and PATCH answering `patch`.
function backend(patch: (body: unknown) => Response | Promise<Response>) {
  const calls = { list: 0, patched: [] as unknown[] };
  server.use(
    http.get(TARGET_URL, () => jsonResponse(OLD)),
    http.get(LIST_URL, () => {
      calls.list += 1;
      return jsonResponse({ items: [OLD] });
    }),
    http.patch(TARGET_URL, async ({ request }) => {
      const body = await request.json();
      calls.patched.push(body);
      return patch(body);
    }),
  );
  return calls;
}

async function openEditor() {
  render(<Harness />, { wrapper: queryWrapper(() => {}) });
  fireEvent.click(await screen.findByRole("button", { name: "Rename target" }));
  return screen.getByLabelText<HTMLInputElement>("Target name");
}

function heading() {
  return screen.getByRole("heading", { level: 1 }).textContent;
}

describe("renaming a target", () => {
  it("saves the new name, shows it, and reloads the sidebar list", async () => {
    const calls = backend(() => jsonResponse({ ...OLD, name: "Mom's spot" }));
    const field = await openEditor();
    await waitFor(() => expect(calls.list).toBe(1));

    fireEvent.change(field, { target: { value: "Mom's spot" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByRole("button", { name: "Rename target" });
    expect(heading()).toBe("Mom's spot");
    expect(calls.patched).toEqual([{ name: "Mom's spot" }]);
    expect(calls.list).toBe(2);
    expect(showToast).toHaveBeenCalledWith("Target renamed.");
  });

  it("Cancel keeps the old name and sends nothing", async () => {
    const calls = backend(() => jsonResponse(OLD));
    const field = await openEditor();

    fireEvent.change(field, { target: { value: "Something else" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(heading()).toBe("Joe's Pizza");
    expect(screen.queryByLabelText("Target name")).toBeNull();
    expect(calls.patched).toEqual([]);
  });

  it("Escape cancels too, and the next edit starts from the saved name", async () => {
    const calls = backend(() => jsonResponse(OLD));
    const field = await openEditor();

    fireEvent.change(field, { target: { value: "Something else" } });
    fireEvent.keyDown(field, { key: "Escape" });

    expect(heading()).toBe("Joe's Pizza");
    expect(calls.patched).toEqual([]);
    fireEvent.click(screen.getByRole("button", { name: "Rename target" }));
    expect(screen.getByLabelText<HTMLInputElement>("Target name").value).toBe("Joe's Pizza");
  });

  it("a bad name shows the backend's message under the field and keeps the edit open", async () => {
    backend(() =>
      jsonResponse(
        {
          error: {
            code: "VALIDATION_ERROR",
            message: "Please fix the highlighted fields.",
            details: [{ field: "name", message: "The name needs at least one letter or number." }],
          },
        },
        422,
      ),
    );
    const field = await openEditor();

    fireEvent.change(field, { target: { value: "!!!" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const message = await screen.findByRole("alert");
    expect(message.textContent).toContain("The name needs at least one letter or number.");
    expect(field.value).toBe("!!!");
    expect(field.getAttribute("aria-invalid")).toBe("true");
    expect(field.getAttribute("aria-describedby")).toBe(message.id);
    await waitFor(() => expect(document.activeElement).toBe(field));
    expect(heading()).toBe("Joe's Pizza");
  });

  it("another failure shows one readable message", async () => {
    backend(() => apiErrorResponse(404, "NOT_FOUND", "Target not found."));
    const field = await openEditor();

    fireEvent.change(field, { target: { value: "Mom's spot" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect((await screen.findByRole("alert")).textContent).toContain("Target not found.");
  });

  it("shows Saving… and locks the form until the request finishes", async () => {
    let finish = () => {};
    backend(
      () =>
        new Promise<Response>((resolve) => {
          finish = () => resolve(jsonResponse({ ...OLD, name: "Mom's spot" }));
        }),
    );
    const field = await openEditor();

    fireEvent.change(field, { target: { value: "Mom's spot" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    const saving = await screen.findByRole("button", { name: "Saving…" });
    expect(saving.hasAttribute("disabled")).toBe(true);
    expect(field.disabled).toBe(true);
    expect(screen.getByRole("button", { name: "Cancel" }).hasAttribute("disabled")).toBe(true);

    finish();
    await screen.findByRole("button", { name: "Rename target" });
  });
});
