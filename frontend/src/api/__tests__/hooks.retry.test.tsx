import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// Transport mocked to FAIL. The expensive action-queries set retry:false, so a failed draft/extract must fire
// EXACTLY ONCE — never auto-retry. This is the cost-safety guard for the Opus research pass: a retry re-runs the
// whole web-search loop and re-spends (the $8-and-nothing amplification).
const h = vi.hoisted(() => ({
  post: vi.fn(async () => ({ data: null, error: { detail: "boom" } })),
  get: vi.fn(async () => ({ data: null, error: { detail: "boom" } })),
}));
vi.mock("../client", () => ({ api: { POST: h.post, GET: h.get } }));

import { useDraftChain, useExtract } from "../hooks";

// The wrapper QueryClient does NOT override retry — so we exercise the HOOK's own retry:false. A default
// QueryClient retries 3x (4 calls); retry:false makes it exactly one. `await refetch()` settles the query
// (including any retries), so the mock call count is final + deterministic (no React-render observation needed).
function freshWrapper() {
  const qc = new QueryClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("expensive action-queries never auto-retry (retry:false)", () => {
  it("useDraftChain fires exactly once on failure (no retry)", async () => {
    const { result } = renderHook(() => useDraftChain("thesis-1"), { wrapper: freshWrapper() });
    await act(async () => {
      await result.current.refetch().catch(() => {});
    });
    expect(h.post).toHaveBeenCalledTimes(1);
  });

  it("useExtract fires exactly once on failure (no retry)", async () => {
    const { result } = renderHook(() => useExtract("sec-1"), { wrapper: freshWrapper() });
    await act(async () => {
      await result.current.refetch().catch(() => {});
    });
    expect(h.get).toHaveBeenCalledTimes(1);
  });
});
