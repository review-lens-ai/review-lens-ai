import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // Same "@/..." import alias as tsconfig.json.
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    // Component tests opt in to a DOM with a `@vitest-environment jsdom` comment.
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
