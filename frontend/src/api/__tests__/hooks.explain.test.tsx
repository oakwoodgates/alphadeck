import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The transport, mocked: a POST returning a grounded explanation. The REAL useExplainFlag runs — we assert
// it POSTs the CANDIDATE to the explain endpoint, on the explicit refetch only (the explanation rides its
// own rail — the /facts/explain endpoint — never the ratify body).
const h = vi.hoisted(() => ({
  post: vi.fn(async () => ({
    data: { explanation: "one-time milestone; recurring is lower", grounded: true },
    error: null,
  })),
}));
vi.mock("../client", () => ({ api: { POST: h.post } }));

import { useExplainFlag, type ExtractedFact } from "../hooks";

const CANDIDATE: ExtractedFact = {
  fact_type: "cash_burn",
  tier: "flag",
  source: "10-q-cashflow",
  source_ref: "https://sec.gov/smr-10q#p1",
  event_date: "2026-03-31",
  note: "",
  cash_usd: 890000000,
  quarterly_burn_usd: 314678000,
  flags: ["possible-one-time"],
  located_passages: [
    { kind: "cash-flow-line", source_ref: "https://sec.gov/smr-10q#p1", anchor: "264,195", excerpt: "…" },
  ],
};

describe("useExplainFlag", () => {
  it("POSTs the candidate to the explain endpoint on the explicit refetch (never on mount)", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useExplainFlag(CANDIDATE), { wrapper });
    expect(h.post).not.toHaveBeenCalled(); // enabled:false — nothing fires on render

    result.current.refetch();
    await waitFor(() =>
      expect(h.post).toHaveBeenCalledWith("/workbench/facts/explain", { body: CANDIDATE }),
    );
  });
});
