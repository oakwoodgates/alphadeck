import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Leaders read: GETs the dashboard's path with asof, group_by and include_silent threaded; each is part of
// the key, so a flip refetches with the new params.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { names: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useLeaders, type GroupBy } from "../useLeaders";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useLeaders", () => {
  it("GETs the dashboard path with every view param, and refetches on a change", async () => {
    const { result, rerender } = renderHook(
      ({ asof, g, s }: { asof: string; g: GroupBy; s: boolean }) => useLeaders(asof, g, s),
      { wrapper, initialProps: { asof: "2026-10-28", g: "link" as GroupBy, s: false } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/leaders", {
      params: { query: { asof: "2026-10-28", group_by: "link", include_silent: false } },
    });

    rerender({ asof: "2026-06-01", g: "card", s: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/leaders", {
      params: { query: { asof: "2026-06-01", group_by: "card", include_silent: true } },
    });
  });
});
