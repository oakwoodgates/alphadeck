import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Market Map read: GETs the dashboard's path with the as-of only. Every control is applied on the page, so a
// flip refetches nothing; a new as-of refetches.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { companies: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useMarketMap } from "../useMarketMap";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useMarketMap", () => {
  it("GETs the dashboard path with the as-of, and refetches on a new as-of", async () => {
    const { result, rerender } = renderHook(({ asof }: { asof: string }) => useMarketMap(asof), {
      wrapper,
      initialProps: { asof: "2026-10-28" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/market-map", { params: { query: { asof: "2026-10-28" } } });

    rerender({ asof: "2026-06-01" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/market-map", { params: { query: { asof: "2026-06-01" } } });
  });
});
