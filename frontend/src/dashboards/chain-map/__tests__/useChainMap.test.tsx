import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Chain Map's two reads: the picker GETs the cards at the as-of; the picture GETs one card at the as-of and
// waits for a card. The Context/Bench toggle is client side, so it is not in the request.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { columns: [], cards: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useChainMap, useChainMapCards } from "../useChainMap";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useChainMapCards", () => {
  it("GETs the picker at the as-of", async () => {
    const { result } = renderHook(() => useChainMapCards("2026-10-28"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/chain-map/cards", {
      params: { query: { asof: "2026-10-28" } },
    });
  });
});

describe("useChainMap", () => {
  it("waits for a card, then GETs it at the as-of, and refetches when the card changes", async () => {
    h.get.mockClear();
    const { result, rerender } = renderHook(({ id }: { id: string | null }) => useChainMap("2026-10-28", id), {
      wrapper,
      initialProps: { id: null as string | null },
    });
    expect(h.get).not.toHaveBeenCalled();

    rerender({ id: "t-nuke" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/chain-map/{thesis_id}", {
      params: { path: { thesis_id: "t-nuke" }, query: { asof: "2026-10-28" } },
    });

    rerender({ id: "t-grid" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/chain-map/{thesis_id}", {
      params: { path: { thesis_id: "t-grid" }, query: { asof: "2026-10-28" } },
    });
  });
});
