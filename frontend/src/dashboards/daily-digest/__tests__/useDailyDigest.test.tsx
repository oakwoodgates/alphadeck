import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The Daily Digest's one read: the as-of is the only request parameter (the tape's toggle is client side). The
// warm-up (backend/pipeline/warm_dashboards.py) mirrors this exact query, and its drift guards read the hook's request
// (useDailyDigest.ts), so a parameter added there is added to the warm-up's PAGES too.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { positions: [] }, error: null as unknown })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useDailyDigest } from "../useDailyDigest";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => h.get.mockClear());

describe("useDailyDigest", () => {
  it("GETs the page at the as-of and refetches when the as-of changes", async () => {
    const { result, rerender } = renderHook(({ asof }: { asof: string }) => useDailyDigest(asof), {
      wrapper,
      initialProps: { asof: "2026-09-17" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/daily-digest", { params: { query: { asof: "2026-09-17" } } });
    rerender({ asof: "2026-09-18" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/daily-digest", { params: { query: { asof: "2026-09-18" } } });
  });

  it("throws the API's error so the page can say it could not load", async () => {
    h.get.mockResolvedValueOnce({ data: undefined as never, error: { detail: "boom" } });
    const { result } = renderHook(() => useDailyDigest("2026-09-17"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
