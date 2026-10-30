import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Trigger Log's one read: it GETs the page at the as-of with the window and the role threaded (all three in
// the key, so a change of any refetches). The grouping, the filters and the sorts are client side, so they are not
// request parameters.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useTriggerLog, type TriggerLogDays, type TriggerLogRole } from "../useTriggerLog";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useTriggerLog", () => {
  it("GETs the page with the as-of, the window and the role, and refetches on a change of any", async () => {
    const { result, rerender } = renderHook(
      ({ asof, d, role }: { asof: string; d: TriggerLogDays; role: TriggerLogRole }) => useTriggerLog(asof, d, role),
      { wrapper, initialProps: { asof: "2026-10-28", d: 90 as TriggerLogDays, role: "entry" as TriggerLogRole } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/trigger-log", {
      params: { query: { asof: "2026-10-28", days: 90, role: "entry" } },
    });

    rerender({ asof: "2026-10-28", d: 180, role: "entry" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/trigger-log", {
      params: { query: { asof: "2026-10-28", days: 180, role: "entry" } },
    });

    rerender({ asof: "2026-10-21", d: 180, role: "entry" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(3));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/trigger-log", {
      params: { query: { asof: "2026-10-21", days: 180, role: "entry" } },
    });

    // each role is its own fetch
    rerender({ asof: "2026-10-21", d: 180, role: "risk" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(4));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/trigger-log", {
      params: { query: { asof: "2026-10-21", days: 180, role: "risk" } },
    });
    rerender({ asof: "2026-10-21", d: 180, role: "both" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(5));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/trigger-log", {
      params: { query: { asof: "2026-10-21", days: 180, role: "both" } },
    });
  });

  it("throws the API's error so the page can say it could not load", async () => {
    h.get.mockResolvedValueOnce({ data: undefined, error: { detail: "boom" } } as never);
    const { result } = renderHook(() => useTriggerLog("2026-10-27", 30, "entry"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
