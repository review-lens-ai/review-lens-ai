import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Two kinds of tests (coding-standards.md §8). `npm test` runs both, and so does CI.
export default defineConfig({
  resolve: {
    // Same "@/..." import alias as tsconfig.json.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    // Changed-code coverage gate (coding-standards.md §8, scripts/coverage.sh).
    // `include` counts every source file, so a page no test loads shows as uncovered
    // instead of missing from the report.
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      // Generated code (shadcn components, the Orval API client), and the test helpers themselves.
      exclude: ["src/app/_components/ui/**", "src/lib/generated/**", "src/test/**", "**/*.test.*"],
      reporter: ["text-summary", "lcov"],
    },
    projects: [
      {
        // Unit tests: one function at a time, in plain Node.
        extends: true,
        test: {
          name: "unit",
          environment: "node",
          include: ["src/**/*.test.ts"],
          exclude: ["src/**/*.integration.test.*"],
        },
      },
      {
        // Integration tests: a whole page rendered in a pretend browser (jsdom),
        // with the backend faked by MSW (src/test/server.ts).
        extends: true,
        test: {
          name: "integration",
          environment: "jsdom",
          include: ["src/**/*.integration.test.tsx"],
          setupFiles: ["src/test/integration-setup.ts"],
          // lib/api.ts needs this as soon as it is imported. MSW answers it.
          env: { NEXT_PUBLIC_API_URL: "http://api.test" },
        },
      },
    ],
  },
});
