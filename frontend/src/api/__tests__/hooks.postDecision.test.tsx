import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The transport, mocked: a POST that succeeds. The REAL usePostDecision runs — we assert its
// onSuccess invalidates all three reads a landed decision changes: the decision log, the call
// (take → Managing), and the THESIS DETAIL (#3 — ThesisDetail.position feeds the per-name panel's
// "Position · this name" block; without this invalidation it went stale after a take/close).
const h = vi.hoisted(() => ({
  post: vi.fn(async () => ({
    data: { id: "d1", action: "take", decision_date: "2026-07-21" },
    error: null,
  })),
}));
vi.mock("../client", () => ({ api: { POST: h.post } }));

import { usePostDecision } from "../hooks";

describe("usePostDecision", () => {
  it("invalidates the decision log, the call, AND the thesis detail on a landed decision", async () => {
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const spy = vi.spyOn(qc, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => usePostDecision("t-1"), { wrapper });
    result.current.mutate({ action: "take", decision_date: "2026-07-21", price: 12.5 });

    await waitFor(() => expect(h.post).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith({ queryKey: ["decisions", "t-1"] });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["call", "t-1"] });
    // the #3 fix — the per-name panel's position now refreshes when a fill lands
    expect(spy).toHaveBeenCalledWith({ queryKey: ["thesis", "t-1"] });
  });
});
