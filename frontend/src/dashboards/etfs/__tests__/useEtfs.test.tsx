import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The ETFs read: GETs the dashboard's path with the as-of and the window threaded; both are part of the key, so
// a change refetches with the new params.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { compare: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useEtfs, type EtfWindow } from "../useEtfs";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useEtfs", () => {
  it("GETs the dashboard path with the as-of and the window, and refetches on a change", async () => {
    const { result, rerender } = renderHook(
      ({ asof, w }: { asof: string; w: EtfWindow }) => useEtfs(asof, w),
      { wrapper, initialProps: { asof: "2026-10-28", w: "1m" as EtfWindow } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/etfs", {
      params: { query: { asof: "2026-10-28", window: "1m" } },
    });

    rerender({ asof: "2026-06-01", w: "3m" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/etfs", {
      params: { query: { asof: "2026-06-01", window: "3m" } },
    });
  });
});
