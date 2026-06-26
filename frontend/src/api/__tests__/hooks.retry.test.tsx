import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Transport mocked to FAIL. The expensive action-hooks set retry:false, so a failed draft/extract must fire
// EXACTLY ONCE — never auto-retry. This is the cost-safety guard for the Opus research pass: a retry re-runs the
// whole web-search loop and re-spends (the $8-and-nothing amplification).
const h = vi.hoisted(() => ({
  post: vi.fn(async () => ({ data: null, error: { detail: "boom" } })),
  get: vi.fn(async () => ({ data: null, error: { detail: "boom" } })),
}));
vi.mock("../client", () => ({ api: { POST: h.post, GET: h.get } }));

import { useDraftJobStatus, useExtract, useStartDraft } from "../hooks";

// The wrapper QueryClient does NOT override retry — so we exercise the HOOK's own retry:false. A default
// QueryClient retries 3x (4 calls); retry:false makes it exactly one.
function freshWrapper() {
  const qc = new QueryClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  h.post.mockClear();
  h.get.mockClear();
});

describe("expensive action-hooks never auto-retry (retry:false)", () => {
  it("useStartDraft (kick-off mutation) fires exactly once on failure (no retry)", async () => {
    const { result } = renderHook(() => useStartDraft("thesis-1"), { wrapper: freshWrapper() });
    await act(async () => {
      await result.current.mutateAsync().catch(() => {});
    });
    expect(h.post).toHaveBeenCalledTimes(1);
  });

  it("useDraftJobStatus (poll) fires exactly once on a 404 (no retry, no re-poll)", async () => {
    const { result } = renderHook(() => useDraftJobStatus("thesis-1", "job-1"), {
      wrapper: freshWrapper(),
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(1); // retry:false + refetchInterval stops on a non-running status
  });

  it("useExtract fires exactly once on failure (no retry)", async () => {
    const { result } = renderHook(() => useExtract("sec-1"), { wrapper: freshWrapper() });
    await act(async () => {
      await result.current.refetch().catch(() => {});
    });
    expect(h.get).toHaveBeenCalledTimes(1);
  });
});
