import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Read-Throughs' one read: a GET of the whole table at the as-of, and nothing else in the request. The tab,
// the sort and the filters are client side, so they are not request parameters (and not in the key).
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useReadThrough } from "../useReadThrough";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useReadThrough", () => {
  it("GETs the table at the as-of and refetches when the as-of changes", async () => {
    const { result, rerender } = renderHook(({ asof }: { asof: string }) => useReadThrough(asof), {
      wrapper,
      initialProps: { asof: "2026-10-28" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/read-through", {
      params: { query: { asof: "2026-10-28" } },
    });

    rerender({ asof: "2026-10-21" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/read-through", {
      params: { query: { asof: "2026-10-21" } },
    });
  });

  it("surfaces an API error as the query's error", async () => {
    h.get.mockResolvedValueOnce({ data: undefined as never, error: { detail: "boom" } as never });
    const { result } = renderHook(() => useReadThrough("2026-10-27"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
