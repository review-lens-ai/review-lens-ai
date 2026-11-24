// S3-001: the sidebar shows each saved analysis by the name the user chose, never by
// its source link. The real AppShell and useTargets run; MSW plays the backend.
import { describe, expect, it, vi } from "vitest";
import { API, http, jsonResponse, queryWrapper, render, screen, server, target } from "@/test";

const router = { push: vi.fn(), replace: vi.fn(), refresh: vi.fn() };

vi.mock("next/navigation", () => ({
  usePathname: () => "/targets",
  useRouter: () => router,
}));
// jsdom has no window.matchMedia, which the sidebar uses to pick its phone layout.
vi.mock("@/lib/hooks/use-mobile", () => ({ useIsMobile: () => false }));
vi.mock("@/lib/supabase/client", () => ({
  createClient: () => ({ auth: { getSession: async () => ({ data: { session: null } }) } }),
}));

import { AppShell } from "./app-shell";

const PIZZA = target({
  id: "target-pizza",
  name: "Mom's favorite spot",
  source_url: "https://www.google.com/maps/place/Joes+Pizza",
});
const CAFE = target({
  id: "target-cafe",
  name: "Downtown coffee",
  source_url: "https://www.google.com/maps/place/Blue+Bottle+Cafe",
});

function backendHas(...targets: ReturnType<typeof target>[]) {
  server.use(
    http.get(`${API}/api/v1/targets`, () => jsonResponse({ items: targets })),
    http.get(`${API}/api/v1/me`, () => jsonResponse({ id: "user-a", email: "a@example.com" })),
  );
}

function openShell() {
  render(
    <AppShell>
      <p>Page content</p>
    </AppShell>,
    { wrapper: queryWrapper(() => router.replace("/login")) },
  );
}

describe("the analysis sidebar", () => {
  it("shows each saved target's name", async () => {
    backendHas(PIZZA, CAFE);
    openShell();

    // findByText / getByText fail the test if the text is not on the page.
    await screen.findByText("Mom's favorite spot");
    screen.getByText("Downtown coffee");
  });

  it("shows the name, not the source link", async () => {
    backendHas(PIZZA, CAFE);
    openShell();

    await screen.findByText("Mom's favorite spot");

    const page = document.body.textContent ?? "";
    expect(page).not.toContain(PIZZA.source_url);
    expect(page).not.toContain(CAFE.source_url);
    expect(page).not.toContain("Joes+Pizza");
  });

  it("each name links to its own target", async () => {
    backendHas(PIZZA, CAFE);
    openShell();

    const pizza = await screen.findByRole("link", { name: /Mom's favorite spot/ });
    const cafe = screen.getByRole("link", { name: /Downtown coffee/ });

    expect(pizza.getAttribute("href")).toBe("/targets/target-pizza");
    expect(cafe.getAttribute("href")).toBe("/targets/target-cafe");
  });
});
