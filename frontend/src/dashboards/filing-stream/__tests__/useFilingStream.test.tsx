import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Filing Stream's two reads: the picker GETs the cards at the as-of; the stream GETs one card with the window
// threaded (in the key, so a change refetches) and waits for a card. The filters and the fold are client side, so
// they are not request parameters.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { groups: [], cards: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useFilingStream, useFilingStreamCards, type StreamDays } from "../useFilingStream";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useFilingStreamCards", () => {
  it("GETs the picker at the as-of", async () => {
    const { result } = renderHook(() => useFilingStreamCards("2026-10-28"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/filing-stream/cards", {
      params: { query: { asof: "2026-10-28" } },
    });
  });
});

describe("useFilingStream", () => {
  it("waits for a card, then GETs it with the as-of and the window, and refetches on a change", async () => {
    h.get.mockClear();
    const { result, rerender } = renderHook(
      ({ id, d }: { id: string | null; d: StreamDays }) => useFilingStream("2026-10-28", id, d),
      { wrapper, initialProps: { id: null as string | null, d: 90 as StreamDays } },
    );
    expect(h.get).not.toHaveBeenCalled();

    rerender({ id: "t-nuke", d: 90 });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/filing-stream/{thesis_id}", {
      params: { path: { thesis_id: "t-nuke" }, query: { asof: "2026-10-28", days: 90 } },
    });

    rerender({ id: "t-nuke", d: 365 });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/filing-stream/{thesis_id}", {
      params: { path: { thesis_id: "t-nuke" }, query: { asof: "2026-10-28", days: 365 } },
    });
  });
});
