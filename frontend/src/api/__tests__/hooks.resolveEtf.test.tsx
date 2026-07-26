import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The transport, mocked: the surface-ETF resolve POST succeeds and returns the row marked 'etf'. The REAL
// useResolveEtf runs — we assert it POSTs the ticker to the right path and hands back the SecurityMatchOut
// (ETF Sleeve, Slice 1). No cache invalidation here (the caller fires ingest-prices after adding the member).
const h = vi.hoisted(() => ({
  post: vi.fn(async () => ({
    data: {
      security_id: "s-lit",
      ticker: "LIT",
      name: "Global X Lithium & Battery Tech ETF",
      cik: null,
      instrument_kind: "etf",
    },
    error: null,
  })),
}));
vi.mock("../client", () => ({ api: { POST: h.post } }));

import { useResolveEtf } from "../hooks";

describe("useResolveEtf", () => {
  it("POSTs the ticker to /workbench/securities/resolve-etf and returns the marked row", async () => {
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useResolveEtf(), { wrapper });
    result.current.mutate("LIT");

    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.post).toHaveBeenCalledWith("/workbench/securities/resolve-etf", {
      body: { ticker: "LIT" },
    });
    expect(result.current.data?.instrument_kind).toBe("etf");
    expect(result.current.data?.cik).toBeNull();
  });
});
