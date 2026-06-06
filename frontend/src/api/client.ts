import createClient from "openapi-fetch";

import type { paths } from "./types.gen";

// Typed against the generated OpenAPI paths. Relative baseUrl: dev proxies to the backend
// (see vite.config.ts), prod serves the SPA same-origin as the API.
export const api = createClient<paths>({ baseUrl: "/" });
