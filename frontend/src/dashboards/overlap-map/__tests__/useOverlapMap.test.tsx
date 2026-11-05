import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Overlap Map read: it GETs the dashboard's path with asof and include_silent threaded (each in the key, so a
// change refetches). The threshold, the tabs and the Venn picks are client-side and never refetch.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { cards: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useOverlapMap } from "../useOverlapMap";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useOverlapMap", () => {
  it("GETs the dashboard path with every view param, and refetches on a change", async () => {
    const { result, rerender } = renderHook(({ asof, s }: { asof: string; s: boolean }) => useOverlapMap(asof, s), {
      wrapper,
      initialProps: { asof: "2026-11-04", s: false },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/overlap-map", {
      params: { query: { asof: "2026-11-04", include_silent: false } },
    });

    rerender({ asof: "2026-06-01", s: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/overlap-map", {
      params: { query: { asof: "2026-06-01", include_silent: true } },
    });
  });
});
