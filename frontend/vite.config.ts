/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Dev: proxy API calls to the FastAPI backend so the SPA can use same-origin relative URLs (no CORS).
export default defineConfig({
  plugins: [react()],
  build: {
    // A dashboard's thumbnail.svg (docs/DASHBOARDS.md, "Thumbnails") stays its own small file. Under 4 KB Vite
    // would inline each drawing into the main chunk as a data URL (about 73 KB raw for 27 of them, measured),
    // and every page loads that chunk. Every other asset keeps Vite's default (undefined = inline under 4 KB).
    assetsInlineLimit: (file) => (/[\\/]thumbnail\.svg$/.test(file) ? false : undefined),
  },
  server: {
    proxy: {
      // The whole API rides /api (see src/api/client.ts) so the SPA owns every other path for its
      // client-side routes. Strip the prefix — the backend never sees /api. Keep in sync with nginx.conf.
      // The long timeout (ms) covers the workbench draft-chain research pass (Opus + web search, minutes),
      // in concert with nginx + llm_research_timeout_s (300s).
      "/api": {
        target: "http://127.0.0.1:8000",
        rewrite: (p) => p.replace(/^\/api/, ""),
        proxyTimeout: 300_000,
        timeout: 300_000,
      },
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
