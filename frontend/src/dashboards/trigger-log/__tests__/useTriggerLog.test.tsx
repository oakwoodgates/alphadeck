import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Trigger Log's one read: it GETs the page at the as-of with the window threaded (both in the key, so a change
// refetches). The grouping, the filters and the sorts are client side, so they are not request parameters.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useTriggerLog, type TriggerLogDays } from "../useTriggerLog";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useTriggerLog", () => {
  it("GETs the page with the as-of and the window, and refetches on a change of either", async () => {
    const { result, rerender } = renderHook(
      ({ asof, d }: { asof: string; d: TriggerLogDays }) => useTriggerLog(asof, d),
      { wrapper, initialProps: { asof: "2026-10-28", d: 90 as TriggerLogDays } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/trigger-log", {
      params: { query: { asof: "2026-10-28", days: 90 } },
    });

    rerender({ asof: "2026-10-28", d: 180 });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/trigger-log", {
      params: { query: { asof: "2026-10-28", days: 180 } },
    });

    rerender({ asof: "2026-10-21", d: 180 });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(3));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/trigger-log", {
      params: { query: { asof: "2026-10-21", days: 180 } },
    });
  });

  it("throws the API's error so the page can say it could not load", async () => {
    h.get.mockResolvedValueOnce({ data: undefined, error: { detail: "boom" } } as never);
    const { result } = renderHook(() => useTriggerLog("2026-10-27", 30), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
