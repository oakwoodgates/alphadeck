/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev: proxy API calls to the FastAPI backend so the SPA can use same-origin relative URLs (no CORS).
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/theses": "http://127.0.0.1:8000",
      "/workbench": "http://127.0.0.1:8000",
      "/health": "http://127.0.0.1:8000",
    },
  },
  // The first frontend test harness (Slice 4b-2): jsdom + RTL for the Workbench authoring components and
  // the grouped-render coverage the flat seed left unexercised in S4. Tests live next to the code; they
  // are excluded from the app's `tsc -b` build (see tsconfig.app.json) and run via `npm test`.
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
