import path from "node:path";

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  // Next.js resolves "@/..." from tsconfig `paths`; vitest does not read
  // those, so any module importing through the alias was untestable —
  // which is why services/analyticsApi.ts had no unit tests.
  resolve: {
    alias: { "@": path.resolve(__dirname, "./src") },
  },
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "lcov", "html"],
      include: ["src/**/*.{ts,tsx}"],
      exclude: [
        "src/__tests__/setup.ts",
        "src/**/*.d.ts",
        "node_modules",
        ".next",
      ],
      thresholds: {
        statements: 60,
        branches: 50,
        functions: 50,
        lines: 60,
      },
    },
    css: { modules: { classNameStrategy: "non-scoped" } },
  },
});
