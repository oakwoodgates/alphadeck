import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The Hot Board's reads: the as-of and which rows to carry are the only request parameters (the toggles, filters,
// sorts and heat are client side). The default read is the armed rows; the full read waits until it is enabled.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [] }, error: null as unknown })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useHotBoard } from "../useHotBoard";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => h.get.mockClear());

describe("useHotBoard", () => {
  it("GETs the armed read at the as-of by default and refetches when the as-of changes", async () => {
    const { result, rerender } = renderHook(({ asof }: { asof: string }) => useHotBoard(asof), {
      wrapper,
      initialProps: { asof: "2026-10-28" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/hot-board", { params: { query: { asof: "2026-10-28", rows: "armed" } } });
    rerender({ asof: "2026-10-21" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/hot-board", { params: { query: { asof: "2026-10-21", rows: "armed" } } });
  });

  it("asks for the full read with rows=all, and sends nothing while it is held back", async () => {
    const { result, rerender } = renderHook(
      ({ enabled }: { enabled: boolean }) => useHotBoard("2026-10-28", "all", enabled),
      { wrapper, initialProps: { enabled: false } },
    );
    await new Promise((r) => setTimeout(r, 20));
    expect(h.get).not.toHaveBeenCalled();
    expect(result.current.fetchStatus).toBe("idle");
    rerender({ enabled: true });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(1);
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/hot-board", { params: { query: { asof: "2026-10-28", rows: "all" } } });
  });

  it("keeps the armed read and the full read apart (two queries, two requests)", async () => {
    const { result } = renderHook(
      () => ({ armed: useHotBoard("2026-10-28", "armed"), all: useHotBoard("2026-10-28", "all") }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.armed.isSuccess && result.current.all.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(2);
    const rows = h.get.mock.calls.map((c) => (c as unknown as [string, { params: { query: { rows: string } } }])[1].params.query.rows);
    expect(rows.sort()).toEqual(["all", "armed"]);
  });

  it("throws the API's error so the page can say it could not load", async () => {
    h.get.mockResolvedValueOnce({ data: undefined as never, error: { detail: "boom" } });
    const { result } = renderHook(() => useHotBoard("2026-10-28"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
