import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The Track Record's one read: the page's whole read at the as-of for one edition (0 = the record). The slice, the
// filters, the entry toggle and the comparison are client side, so none of them is a request parameter.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useTrackRecord } from "../useTrackRecord";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => h.get.mockClear());

describe("useTrackRecord", () => {
  it("GETs the whole page at the as-of for the edition, and refetches when either changes", async () => {
    const { result, rerender } = renderHook(
      ({ asof, edition }: { asof: string; edition: number }) => useTrackRecord(asof, edition),
      { wrapper, initialProps: { asof: "2026-10-28", edition: 0 } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    // the record is asked for by name: edition 0
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/track-record", {
      params: { query: { asof: "2026-10-28", edition: 0 } },
    });
    rerender({ asof: "2026-10-21", edition: 0 });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/track-record", {
      params: { query: { asof: "2026-10-21", edition: 0 } },
    });
    // an edition is its own read
    rerender({ asof: "2026-10-21", edition: 1 });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(3));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/track-record", {
      params: { query: { asof: "2026-10-21", edition: 1 } },
    });
  });

  it("surfaces an API error as the query's error", async () => {
    h.get.mockResolvedValueOnce({ data: undefined as never, error: { detail: "boom" } as never });
    const { result } = renderHook(() => useTrackRecord("2026-10-27", 0), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
