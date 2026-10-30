import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Entry Windows' one read: a GET at the as-of, refetched when the as-of moves. The Managing toggle and the
// sorts are client side, so neither is in the request.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useEntryWindows } from "../useEntryWindows";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useEntryWindows", () => {
  it("GETs the page at the as-of and refetches when the as-of changes", async () => {
    const { result, rerender } = renderHook(({ asof }: { asof: string }) => useEntryWindows(asof), {
      wrapper,
      initialProps: { asof: "2026-10-28" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/entry-windows", { params: { query: { asof: "2026-10-28" } } });

    rerender({ asof: "2026-10-20" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/entry-windows", { params: { query: { asof: "2026-10-20" } } });
  });

  it("throws the API error so the page can say it could not load", async () => {
    h.get.mockResolvedValueOnce({ data: undefined as never, error: { detail: "boom" } as never });
    const { result } = renderHook(() => useEntryWindows("2026-10-01"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
