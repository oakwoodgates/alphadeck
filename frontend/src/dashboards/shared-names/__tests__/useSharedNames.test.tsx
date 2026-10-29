import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Shared Names read: GETs the dashboard's path with `counting_only` threaded, and a flip of the toggle is
// a new key, so it refetches with the new param (the rule lives server-side, in one place).
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({
    data: {
      counting_only: false,
      live_cards: 0,
      basket_rows: 0,
      silent_rows_left_out: 0,
      unresolved_rows: 0,
      companies: 0,
      names: [],
      pairs: [],
    },
    error: null,
  })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useSharedNames } from "../useSharedNames";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useSharedNames", () => {
  it("GETs the dashboard path with counting_only, and refetches with the new value on a flip", async () => {
    const { result, rerender } = renderHook(({ c }) => useSharedNames(c), {
      wrapper,
      initialProps: { c: false },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/shared-names", {
      params: { query: { counting_only: false } },
    });

    rerender({ c: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/shared-names", {
      params: { query: { counting_only: true } },
    });
  });
});
