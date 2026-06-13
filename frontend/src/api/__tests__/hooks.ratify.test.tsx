import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The transport, mocked: a POST that succeeds. The REAL useRatifyFact runs — we assert its onSuccess
// invalidates the scored read (Option B: the meter re-derives on the next read; nothing persists).
const h = vi.hoisted(() => ({
  post: vi.fn(async () => ({ data: { fact_id: "f1", fact_type: "cash_burn" }, error: null })),
}));
vi.mock("../client", () => ({ api: { POST: h.post } }));

import { useRatifyFact } from "../hooks";

describe("useRatifyFact", () => {
  it("invalidates the scored read on a successful ratify (re-derive)", async () => {
    const qc = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
    const spy = vi.spyOn(qc, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useRatifyFact(), { wrapper });
    result.current.mutate({
      fact_type: "cash_burn",
      security_id: "00000000-0000-0000-0000-000000000abc",
      source: "10-q-cashflow",
      source_ref: "https://sec.gov/smr-10q",
      event_date: "2026-03-31",
      note: "recurring only",
      cash_usd: 890000000,
      quarterly_burn_usd: 50483000,
    });

    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith({ queryKey: ["workbench-scored"] }),
    );
    expect(h.post).toHaveBeenCalledWith("/workbench/facts", expect.objectContaining({ body: expect.any(Object) }));
  });
});
