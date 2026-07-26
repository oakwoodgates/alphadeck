import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The holdings query (ETF Sleeve, Slice 2a): `enabled:false` — mounting NEVER fetches (the pull is the
// drawer's deliberate click, the cost thread); `refetch()` GETs the etf-holdings path with the thesis +
// as-of threaded (the overlap is thesis-scoped, #2; asof keeps the filing knowable-then, #1).
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({
    data: {
      report_date: "2026-04-30",
      source_ref: "https://www.sec.gov/…-index.htm",
      holdings_count: 45,
      held: [],
      available: [],
      unresolved: [],
    },
    error: null,
  })),
}));
vi.mock("../client", () => ({ api: { GET: h.get } }));

import { useEtfHoldings } from "../hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useEtfHoldings", () => {
  it("does not fetch on mount; refetch GETs the path with thesis_id + asof threaded", async () => {
    const { result } = renderHook(() => useEtfHoldings("s-lit", "t-1", "2026-06-01"), {
      wrapper,
    });
    expect(h.get).not.toHaveBeenCalled(); // enabled:false — render is free

    result.current.refetch();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledWith("/workbench/securities/{security_id}/etf-holdings", {
      params: {
        path: { security_id: "s-lit" },
        query: { thesis_id: "t-1", asof: "2026-06-01" },
      },
    });
    expect(result.current.data?.holdings_count).toBe(45);
  });

  it("omits thesis_id/asof from the query when absent (holdings-only mode)", async () => {
    const { result } = renderHook(() => useEtfHoldings("s-lit"), { wrapper });
    result.current.refetch();
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/workbench/securities/{security_id}/etf-holdings", {
      params: { path: { security_id: "s-lit" }, query: {} },
    });
  });
});
