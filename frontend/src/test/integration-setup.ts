// Runs around every integration test (vitest.config.mts, "integration" project).
import { cleanup } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, expect, vi } from "vitest";
import { server } from "./server";

// jsdom has no layout, so it lacks ResizeObserver. assistant-ui's thread viewport uses it
// to keep the newest message in view; in tests nothing ever resizes, so a stub is enough.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// Requests the page made that no handler answered.
const unansweredRequests: string[] = [];

beforeAll(() => {
  server.listen({
    onUnhandledRequest(request, print) {
      unansweredRequests.push(`${request.method} ${request.url}`);
      // Make the request fail, as if the server were down.
      print.error();
    },
  });
});

afterEach(async () => {
  // Remove what the test rendered, so the next test starts with an empty page.
  cleanup();
  // Let requests the page sent just before the test ended reach their handlers,
  // before the handlers are removed below.
  await new Promise((resolve) => setTimeout(resolve, 0));

  // Fail the test if the page called an endpoint the test didn't set up.
  // Pages often hide a failed request, so without this a missing handler goes unnoticed.
  const unanswered = unansweredRequests.splice(0);
  expect(unanswered, "requests with no MSW handler").toEqual([]);

  // Forget the handlers this test added with server.use(...).
  server.resetHandlers();
  // Forget the calls recorded by vi.fn() mocks.
  vi.clearAllMocks();
});

afterAll(() => server.close());
