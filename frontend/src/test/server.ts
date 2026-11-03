// The fake backend for integration tests, using MSW (https://mswjs.io).
// It answers the page's fetch calls without a server. Nothing leaves the test.
//
// Each test lists the endpoints its page calls with server.use(...).
// See coding-standards.md §8 for an example.
import { setupServer } from "msw/node";

// Same value as NEXT_PUBLIC_API_URL in vitest.config.mts.
export const API = "http://api.test";

export const server = setupServer();
