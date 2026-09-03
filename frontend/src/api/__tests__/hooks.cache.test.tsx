import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// The PR-1a cache policy (docs/temp/board-cockpit-perf-2026-09-02.md §4.C), asserted through
// BEHAVIOR (does a request fire?) — never by reading options back out of the query, which would
// only prove the object we just built.
const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get, POST: h.post } }));

import { useCall, useDisplaySignals, useWorkbenchScored } from "../hooks";
import { createQueryClient, MONITOR_STALE_MS } from "../queryClient";

const wrap = (qc: QueryClient) =>
  function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
  };

const card = { thesis_id: "t1", asof: "2026-09-02", state: "incubating" };

beforeEach(() => {
  h.get.mockReset();
  h.get.mockResolvedValue({ data: card, error: null });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("C1a — refetchOnWindowFocus is off by CLIENT DEFAULT", () => {
  // a bare query with the library's own staleTime (0): it is stale the instant it settles, so a
  // focus refetch WOULD fire if the client allowed one
  const probe = () => useQuery({ queryKey: ["probe"], queryFn: () => h.get("/probe") });

  it("createQueryClient(): a window focus fires NO second request", async () => {
    const { result } = renderHook(probe, { wrapper: wrap(createQueryClient()) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    await new Promise((r) => setTimeout(r, 25)); // give a refetch every chance to land
    expect(h.get).toHaveBeenCalledTimes(1);
  });

  it("CONTROL: a stock QueryClient DOES refetch on that same event", async () => {
    // Without this the test above could pass for the wrong reason — e.g. if `visibilitychange` on
    // `window` never reached TanStack's focus manager in jsdom, "no second request" would be
    // vacuously true and the assertion would keep passing after someone deleted the default.
    const { result } = renderHook(probe, { wrapper: wrap(new QueryClient()) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
  });
});

describe("C1b — a MONITOR read stays fresh for MONITOR_STALE_MS", () => {
  it("remount inside the window reuses the cache; past it, one refetch", async () => {
    // shouldAdvanceTime keeps timers flowing (so RTL's waitFor still works) while giving us a clock
    // we can jump forward — TanStack decides staleness from Date.now() - dataUpdatedAt.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const qc = createQueryClient();
    const wrapper = wrap(qc);
    const mount = () => renderHook(() => useCall("t1", "2026-09-02"), { wrapper });

    const first = mount();
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(1);
    first.unmount();

    // INSIDE the window (this is the Board → Cockpit → Back path that used to cost ~27 s of
    // backend work every time)
    const second = mount();
    await waitFor(() => expect(second.result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(1);

    // …and the window really does expire (advance while MOUNTED so gcTime can't evict the entry
    // and turn this into a cache MISS, which would pass for the wrong reason)
    act(() => {
      vi.advanceTimersByTime(MONITOR_STALE_MS + 1_000);
    });
    second.unmount();

    const third = mount();
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(third.result.current.data).toEqual(card);
  });
});

describe("C4 — the compute-heavy reads retry ONCE, not three times", () => {
  // The default retry:3 means a failing /call burns 4× the (17–25 s) compute before the Board's
  // "Calls that didn't compute" strip appears.
  const cases = [
    ["useCall", () => useCall("t1", "2026-09-02")],
    ["useDisplaySignals", () => useDisplaySignals("t1", "2026-09-02")],
    ["useWorkbenchScored", () => useWorkbenchScored("t1", "2026-09-02")],
  ] as const;

  for (const [name, hook] of cases) {
    it(`${name}: a failing transport is called exactly twice`, async () => {
      h.get.mockResolvedValue({ data: undefined, error: { detail: "boom" } });
      // only the retry DELAY is overridden (to keep the test quick) — never the retry COUNT, which
      // is what is under test; the client default here is still 3
      const qc = new QueryClient({ defaultOptions: { queries: { retryDelay: 0 } } });
      const { result } = renderHook(hook, { wrapper: wrap(qc) });
      await waitFor(() => expect(result.current.isError).toBe(true));
      expect(h.get).toHaveBeenCalledTimes(2);
    });
  }
});
