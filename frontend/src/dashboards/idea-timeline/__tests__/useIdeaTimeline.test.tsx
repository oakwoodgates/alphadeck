import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Idea Timeline read: GETs the dashboard's path with no params (a "now" view: no as-of).
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useIdeaTimeline } from "../useIdeaTimeline";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useIdeaTimeline", () => {
  it("GETs the dashboard path with no as-of", async () => {
    const { result } = renderHook(() => useIdeaTimeline(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(1);
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/idea-timeline");
  });
});
