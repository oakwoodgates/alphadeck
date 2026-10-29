import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Catalyst Calendar read: GETs the dashboard's path, no params (a "now" view; the filters are on screen).
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({
    data: {
      today: "2026-10-29",
      live_cards: 0,
      cards_without_calls: 0,
      cards_without_catalysts: 0,
      catalysts: 0,
      dated: 0,
      upcoming_catalysts: 0,
      passed_catalysts: 0,
      undated: 0,
      deadlines: 0,
      buckets: [],
      passed: [],
      undated_groups: [],
    },
    error: null,
  })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useCatalystCalendar } from "../useCatalystCalendar";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useCatalystCalendar", () => {
  it("GETs the dashboard path with no params and returns its payload", async () => {
    const { result } = renderHook(() => useCatalystCalendar(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(1);
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/catalyst-calendar");
    expect(result.current.data?.today).toBe("2026-10-29");
  });
});
