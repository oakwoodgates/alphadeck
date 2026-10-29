import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The Peer Table's two reads: the scope lists at the as-of, and one scope's whole table (the as-of, the scope and
// its key in the request). The sort and the filters are client side, so they are not request parameters.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { companies: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { usePeerScopes, usePeerTable } from "../usePeerTable";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => h.get.mockClear());

describe("usePeerScopes", () => {
  it("GETs the scope lists at the as-of and refetches when the as-of changes", async () => {
    const { result, rerender } = renderHook(({ asof }: { asof: string }) => usePeerScopes(asof), {
      wrapper,
      initialProps: { asof: "2026-10-28" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/peer-table/scopes", {
      params: { query: { asof: "2026-10-28" } },
    });
    rerender({ asof: "2026-10-21" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/peer-table/scopes", {
      params: { query: { asof: "2026-10-21" } },
    });
  });
});

describe("usePeerTable", () => {
  it("GETs one scope's table with the as-of, the scope and its key", async () => {
    const { result } = renderHook(() => usePeerTable("2026-10-28", "business_type", "miner"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/peer-table", {
      params: { query: { asof: "2026-10-28", scope: "business_type", key: "miner" } },
    });
  });

  it("sends no key for the all-cards scope", async () => {
    const { result } = renderHook(() => usePeerTable("2026-10-28", "all", "ignored"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/peer-table", {
      params: { query: { asof: "2026-10-28", scope: "all" } },
    });
  });

  it("refetches when the scope or its key changes, and waits for a scope that needs a key", async () => {
    const { result, rerender } = renderHook(
      ({ scope, key }: { scope: "business_type" | "card" | null; key: string | null }) =>
        usePeerTable("2026-10-28", scope, key),
      { wrapper, initialProps: { scope: null as "business_type" | "card" | null, key: null as string | null } },
    );
    expect(result.current.fetchStatus).toBe("idle"); // no scope yet: nothing is read
    expect(h.get).not.toHaveBeenCalled();
    rerender({ scope: "card", key: null });
    expect(h.get).not.toHaveBeenCalled(); // a card scope with no card picked yet
    rerender({ scope: "card", key: "t-grid" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(1));
    rerender({ scope: "business_type", key: "bank" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/peer-table", {
      params: { query: { asof: "2026-10-28", scope: "business_type", key: "bank" } },
    });
  });

  it("surfaces an API error as the query's error", async () => {
    h.get.mockResolvedValueOnce({ data: undefined as never, error: { detail: "boom" } as never });
    const { result } = renderHook(() => usePeerTable("2026-10-27", "all", null), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
