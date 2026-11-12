import { defineConfig } from "orval";

// Generates src/lib/generated/api.ts from the backend's OpenAPI spec.
// Run `npm run api:generate` after changing a backend route or schema.
export default defineConfig({
  reviewlens: {
    input: "./openapi.json",
    output: {
      mode: "single",
      target: "./src/lib/generated/api.ts",
      client: "react-query",
      httpClient: "fetch",
      override: {
        // Every request goes through apiFetch: it adds the Supabase token and throws ApiError.
        mutator: { path: "./src/lib/api.ts", name: "apiFetch" },
        // apiFetch returns the response body, not { data, status, headers }.
        fetch: { includeHttpResponseReturnType: false },
      },
    },
  },
});
