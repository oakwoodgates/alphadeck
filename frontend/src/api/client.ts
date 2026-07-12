import createClient from "openapi-fetch";

import type { paths } from "./types.gen";

// Typed against the generated OpenAPI paths. The /api prefix is a PROXY-LAYER convention only —
// vite.config.ts (dev) and nginx.conf (prod) strip it before the backend sees the request, so the
// backend routes and the OpenAPI contract never carry it. It exists to keep every non-/api path
// free for the SPA's client-side routes (the backend's own paths — /theses, /scoreboard,
// /workbench — would otherwise shadow them on the shared origin).
export const api = createClient<paths>({ baseUrl: "/api" });
