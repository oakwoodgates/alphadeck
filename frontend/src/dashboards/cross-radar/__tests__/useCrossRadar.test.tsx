import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Cross Radar read: GETs the dashboard's path with asof, group_by, include_silent and price_200d threaded;
// each is part of the key, so a flip refetches with the new params.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { timeline: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useCrossRadar, type GroupBy } from "../useCrossRadar";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useCrossRadar", () => {
  it("GETs the dashboard path with every view param, and refetches on a change", async () => {
    const { result, rerender } = renderHook(
      ({ asof, g, s, p }: { asof: string; g: GroupBy; s: boolean; p: boolean }) => useCrossRadar(asof, g, s, p),
      { wrapper, initialProps: { asof: "2026-10-28", g: "link" as GroupBy, s: false, p: false } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/cross-radar", {
      params: { query: { asof: "2026-10-28", group_by: "link", include_silent: false, price_200d: false } },
    });

    rerender({ asof: "2026-06-01", g: "sector", s: true, p: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/cross-radar", {
      params: { query: { asof: "2026-06-01", group_by: "sector", include_silent: true, price_200d: true } },
    });
  });
});
