// useTargets: the target list the sidebar and the pages share, loaded through the
// generated useListTargets hook. MSW plays the backend.
import { describe, expect, it, vi } from "vitest";
import {
  act,
  API,
  apiErrorResponse,
  http,
  jsonResponse,
  queryWrapper,
  renderHook,
  server,
  target,
  waitFor,
} from "@/test";

vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import { useTargets } from "./use-targets";

const TARGETS_URL = `${API}/api/v1/targets`;

function backendReturns(response: () => Response) {
  server.use(http.get(TARGETS_URL, response));
}

describe("useTargets", () => {
  it("returns the user's targets once they load", async () => {
    backendReturns(() => jsonResponse({ items: [target({ name: "Joe's Pizza" })] }));

    const { result } = renderHook(() => useTargets(), { wrapper: queryWrapper() });

    expect(result.current.targets).toBeNull();
    await waitFor(() => expect(result.current.targets).toHaveLength(1));
    expect(result.current.targets?.[0].name).toBe("Joe's Pizza");
    expect(result.current.error).toBeNull();
  });

  it("refresh loads the list again, so a new target shows up", async () => {
    backendReturns(() => jsonResponse({ items: [target({ id: "a" })] }));
    const { result } = renderHook(() => useTargets(), { wrapper: queryWrapper() });
    await waitFor(() => expect(result.current.targets).toHaveLength(1));

    backendReturns(() => jsonResponse({ items: [target({ id: "b" }), target({ id: "a" })] }));
    await act(() => result.current.refresh());

    // TanStack Query hands new data to components on the next tick, so wait for it.
    await waitFor(() => expect(result.current.targets?.map((t) => t.id)).toEqual(["b", "a"]));
  });

  it("refresh is the same function on every render", async () => {
    // Pages list refresh in a useEffect's dependencies. A new function each render
    // would re-run that effect, which reloads the list, which renders again: a loop.
    backendReturns(() => jsonResponse({ items: [] }));
    const { result, rerender } = renderHook(() => useTargets(), { wrapper: queryWrapper() });
    const first = result.current.refresh;

    rerender();

    expect(result.current.refresh).toBe(first);
  });

  it("shows the backend's message when the list can't load", async () => {
    backendReturns(() =>
      apiErrorResponse(500, "INTERNAL_ERROR", "Something went wrong. Please try again."),
    );

    const { result } = renderHook(() => useTargets(), { wrapper: queryWrapper() });

    await waitFor(() =>
      expect(result.current.error).toBe("Something went wrong. Please try again."),
    );
    expect(result.current.targets).toBeNull();
  });

  it("on a 401 calls the sign-in redirect and shows no error", async () => {
    backendReturns(() => apiErrorResponse(401, "AUTHENTICATION_REQUIRED", "Please sign in."));
    const onUnauthorized = vi.fn();

    const { result } = renderHook(() => useTargets(), { wrapper: queryWrapper(onUnauthorized) });

    await waitFor(() => expect(onUnauthorized).toHaveBeenCalledOnce());
    expect(result.current.error).toBeNull();
  });
});
