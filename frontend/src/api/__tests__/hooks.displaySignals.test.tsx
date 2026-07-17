import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The read-only indicators query (docs/DISPLAY_SIGNALS.md): one GET per (thesis, asof), enabled
// only when both are set — a pure read the Cockpit fetches once and the panel joins by security_id.
const h = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get } }));

import { useDisplaySignals } from "../hooks";

function wrapper({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      {children}
    </QueryClientProvider>
  );
}

beforeEach(() => {
  h.get.mockReset();
  h.get.mockResolvedValue({
    data: { thesis_id: "t1", asof: "2026-07-11", members: [] },
    error: null,
  });
});

describe("useDisplaySignals — the per-name indicators query", () => {
  it("GETs the display-signals path with the thesis + asof", async () => {
    const { result } = renderHook(() => useDisplaySignals("t1", "2026-07-11"), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledWith("/theses/{thesis_id}/display-signals", {
      params: { path: { thesis_id: "t1" }, query: { asof: "2026-07-11" } },
    });
    expect(result.current.data?.members).toEqual([]);
  });

  it("stays disabled (no fetch) until both thesis and asof are set", () => {
    renderHook(() => useDisplaySignals("", "2026-07-11"), { wrapper });
    renderHook(() => useDisplaySignals("t1", ""), { wrapper });
    expect(h.get).not.toHaveBeenCalled();
  });

  it("throws the wire error into the query state (never a silent blank)", async () => {
    h.get.mockResolvedValue({ data: undefined, error: { detail: "boom" } });
    const { result } = renderHook(() => useDisplaySignals("t1", "2026-07-11"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
