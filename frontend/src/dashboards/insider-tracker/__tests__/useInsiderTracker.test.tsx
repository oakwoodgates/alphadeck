import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// Insider Buying's one read: it GETs the page at the as-of with the window and the Context-and-Bench toggle threaded
// (all three in the key, so a change of any refetches). The grouping, the filters and the sorts are client side, so
// they are not request parameters.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useInsiderTracker, type InsiderTrackerDays } from "../useInsiderTracker";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useInsiderTracker", () => {
  it("GETs the page with the as-of, the window and the toggle, and refetches on a change of any", async () => {
    const { result, rerender } = renderHook(
      ({ asof, d, silent }: { asof: string; d: InsiderTrackerDays; silent: boolean }) => useInsiderTracker(asof, d, silent),
      { wrapper, initialProps: { asof: "2026-10-28", d: 30 as InsiderTrackerDays, silent: false } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/insider-tracker", {
      params: { query: { asof: "2026-10-28", days: 30, silent: false } },
    });

    rerender({ asof: "2026-10-28", d: 90, silent: false });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/insider-tracker", {
      params: { query: { asof: "2026-10-28", days: 90, silent: false } },
    });

    rerender({ asof: "2026-10-28", d: 90, silent: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(3));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/insider-tracker", {
      params: { query: { asof: "2026-10-28", days: 90, silent: true } },
    });

    rerender({ asof: "2026-10-21", d: 90, silent: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(4));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/insider-tracker", {
      params: { query: { asof: "2026-10-21", days: 90, silent: true } },
    });
  });

  it("throws the API's error so the page can say it could not load", async () => {
    h.get.mockResolvedValueOnce({ data: undefined, error: { detail: "boom" } } as never);
    const { result } = renderHook(() => useInsiderTracker("2026-10-27", 30, false), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
