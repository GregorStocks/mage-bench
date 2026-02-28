import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "happy-dom",
    exclude: ["tests/e2e/**", "node_modules/**"],
  },
});
