import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Narrative Index reads: the grid GETs the dashboard's path with asof, window and include_silent threaded
// (each in the key, so a change refetches); the enlarged card GETs its own path only once a card is open.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { cards: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useNarrativeCard, useNarrativeIndex, type IndexWindow } from "../useNarrativeIndex";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useNarrativeIndex", () => {
  it("GETs the dashboard path with every view param, and refetches on a change", async () => {
    const { result, rerender } = renderHook(
      ({ asof, w, s }: { asof: string; w: IndexWindow; s: boolean }) => useNarrativeIndex(asof, w, s),
      { wrapper, initialProps: { asof: "2026-10-28", w: "1y" as IndexWindow, s: false } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/narrative-index", {
      params: { query: { asof: "2026-10-28", window: "1y", include_silent: false } },
    });

    rerender({ asof: "2026-06-01", w: "30d", s: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/narrative-index", {
      params: { query: { asof: "2026-06-01", window: "30d", include_silent: true } },
    });
  });
});

describe("useNarrativeCard", () => {
  it("waits for an open card, then GETs its path with the view params", async () => {
    h.get.mockClear();
    const { result, rerender } = renderHook(
      ({ id }: { id: string | null }) => useNarrativeCard("2026-10-28", "90d", false, id),
      { wrapper, initialProps: { id: null as string | null } },
    );
    expect(h.get).not.toHaveBeenCalled();
    rerender({ id: "t-grid" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/narrative-index/{thesis_id}", {
      params: {
        path: { thesis_id: "t-grid" },
        query: { asof: "2026-10-28", window: "90d", include_silent: false },
      },
    });
  });
});
