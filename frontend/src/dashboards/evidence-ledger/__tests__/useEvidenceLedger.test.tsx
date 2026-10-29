import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Evidence Ledger's two reads: the picker GETs the cards at the as-of; the ledger GETs one card at the as-of
// and waits for a card. The filters are client side, so they are not request parameters.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { groups: [], cards: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useEvidenceLedger, useEvidenceLedgerCards } from "../useEvidenceLedger";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useEvidenceLedgerCards", () => {
  it("GETs the picker at the as-of", async () => {
    const { result } = renderHook(() => useEvidenceLedgerCards("2026-10-28"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/evidence-ledger/cards", {
      params: { query: { asof: "2026-10-28" } },
    });
  });
});

describe("useEvidenceLedger", () => {
  it("waits for a card, then GETs it at the as-of, and refetches when the card or the as-of changes", async () => {
    h.get.mockClear();
    const { result, rerender } = renderHook(
      ({ id, asof }: { id: string | null; asof: string }) => useEvidenceLedger(asof, id),
      { wrapper, initialProps: { id: null as string | null, asof: "2026-10-28" } },
    );
    expect(h.get).not.toHaveBeenCalled();

    rerender({ id: "t-nuke", asof: "2026-10-28" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/evidence-ledger/{thesis_id}", {
      params: { path: { thesis_id: "t-nuke" }, query: { asof: "2026-10-28" } },
    });

    rerender({ id: "t-nuke", asof: "2026-10-20" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/evidence-ledger/{thesis_id}", {
      params: { path: { thesis_id: "t-nuke" }, query: { asof: "2026-10-20" } },
    });
  });
});
