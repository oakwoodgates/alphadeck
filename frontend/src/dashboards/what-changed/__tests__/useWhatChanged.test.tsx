import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The What Changed read: GETs the dashboard's path with `since` threaded, and a new "since" is a new key, so
// it refetches with the new param (the baseline rule lives server-side, in one place).
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({
    data: {
      since: "last_night",
      since_date: null,
      first_recorded: null,
      latest: null,
      baseline: null,
      live_cards: 0,
      cards_with_change: 0,
      unrecorded: [],
      items: [],
    },
    error: null,
  })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useWhatChanged } from "../useWhatChanged";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useWhatChanged", () => {
  it("GETs the dashboard path with since, and refetches with each new since", async () => {
    const { result, rerender } = renderHook(({ s }) => useWhatChanged(s), {
      wrapper,
      initialProps: { s: "last_night" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/what-changed", {
      params: { query: { since: "last_night" } },
    });

    rerender({ s: "7d" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/what-changed", {
      params: { query: { since: "7d" } },
    });

    rerender({ s: "2026-10-21" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(3));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/what-changed", {
      params: { query: { since: "2026-10-21" } },
    });
  });
});
