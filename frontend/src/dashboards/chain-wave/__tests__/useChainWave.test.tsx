import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Chain Wave's two reads: the picker GETs the cards at the as-of; the grid GETs one card with the window and
// the Context/Bench toggle threaded (each in the key, so a change refetches) and waits for a card.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [], cards: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useChainWave, useChainWaveCards, type WaveWeeks } from "../useChainWave";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useChainWaveCards", () => {
  it("GETs the picker at the as-of", async () => {
    const { result } = renderHook(() => useChainWaveCards("2026-10-28"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/chain-wave/cards", {
      params: { query: { asof: "2026-10-28" } },
    });
  });
});

describe("useChainWave", () => {
  it("waits for a card, then GETs it with every view param, and refetches on a change", async () => {
    h.get.mockClear();
    const { result, rerender } = renderHook(
      ({ id, w, s }: { id: string | null; w: WaveWeeks; s: boolean }) => useChainWave("2026-10-28", id, w, s),
      { wrapper, initialProps: { id: null as string | null, w: 26 as WaveWeeks, s: false } },
    );
    expect(h.get).not.toHaveBeenCalled();

    rerender({ id: "t-nuke", w: 26, s: false });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/chain-wave/{thesis_id}", {
      params: { path: { thesis_id: "t-nuke" }, query: { asof: "2026-10-28", weeks: 26, include_silent: false } },
    });

    rerender({ id: "t-nuke", w: 52, s: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/chain-wave/{thesis_id}", {
      params: { path: { thesis_id: "t-nuke" }, query: { asof: "2026-10-28", weeks: 52, include_silent: true } },
    });
  });
});
