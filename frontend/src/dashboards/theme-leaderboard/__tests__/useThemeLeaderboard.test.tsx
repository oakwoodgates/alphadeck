import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The Theme Leaderboard's one read: the as-of is the only request parameter (the filters, sorts, percentiles and
// score are client side).
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { cards: [] }, error: null as unknown })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useThemeLeaderboard } from "../useThemeLeaderboard";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => h.get.mockClear());

describe("useThemeLeaderboard", () => {
  it("GETs the leaderboard at the as-of and refetches when the as-of changes", async () => {
    const { result, rerender } = renderHook(({ asof }: { asof: string }) => useThemeLeaderboard(asof), {
      wrapper,
      initialProps: { asof: "2026-10-20" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/theme-leaderboard", { params: { query: { asof: "2026-10-20" } } });
    rerender({ asof: "2026-10-13" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/theme-leaderboard", { params: { query: { asof: "2026-10-13" } } });
  });

  it("throws the API's error so the page can say it could not load", async () => {
    h.get.mockResolvedValueOnce({ data: undefined as never, error: { detail: "boom" } });
    const { result } = renderHook(() => useThemeLeaderboard("2026-10-20"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
