import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

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
});
