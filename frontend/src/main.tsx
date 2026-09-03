import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import { App } from "./App";
import { createQueryClient } from "./api/queryClient";
import "./index.css";

// The cache policy lives in api/queryClient.ts so the tests exercise the SAME defaults the app
// ships (a client hand-rolled in a test proves nothing about what runs here).
const queryClient = createQueryClient();

// Tests render <MemoryRouter><App /></MemoryRouter>, mirroring this shape.
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
