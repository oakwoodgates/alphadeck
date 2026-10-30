import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Narrative Adoption's two reads: the picker GETs the cards at the as-of; the page GETs one card at the as-of
// and waits for a card.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { months: [], cards: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useAdoptionCards, useNarrativeAdoption } from "../useNarrativeAdoption";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useAdoptionCards", () => {
  it("GETs the picker at the as-of", async () => {
    const { result } = renderHook(() => useAdoptionCards("2026-10-28"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/narrative-adoption/cards", {
      params: { query: { asof: "2026-10-28" } },
    });
  });
});

describe("useNarrativeAdoption", () => {
  it("waits for a card, then GETs it at the as-of, and refetches when the card changes", async () => {
    h.get.mockClear();
    const { result, rerender } = renderHook(({ id }: { id: string | null }) => useNarrativeAdoption("2026-10-28", id), {
      wrapper,
      initialProps: { id: null as string | null },
    });
    expect(h.get).not.toHaveBeenCalled();

    rerender({ id: "t-a" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/narrative-adoption/{thesis_id}", {
      params: { path: { thesis_id: "t-a" }, query: { asof: "2026-10-28" } },
    });

    rerender({ id: "t-b" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/narrative-adoption/{thesis_id}", {
      params: { path: { thesis_id: "t-b" }, query: { asof: "2026-10-28" } },
    });
  });
});
