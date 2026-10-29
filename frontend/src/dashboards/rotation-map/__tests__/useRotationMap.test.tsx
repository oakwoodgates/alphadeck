import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

// The Rotation Map read: GETs the dashboard's path with asof, group_by and include_silent threaded; each is
// part of the key, so a flip refetches with the new params.
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { groups: [] }, error: null })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useRotationMap, type GroupBy } from "../useRotationMap";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useRotationMap", () => {
  it("GETs the dashboard path with every view param, and refetches on a change", async () => {
    const { result, rerender } = renderHook(
      ({ asof, g, s }: { asof: string; g: GroupBy; s: boolean }) => useRotationMap(asof, g, s),
      { wrapper, initialProps: { asof: "2026-10-28", g: "link" as GroupBy, s: false } },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/rotation-map", {
      params: { query: { asof: "2026-10-28", group_by: "link", include_silent: false } },
    });

    rerender({ asof: "2026-06-01", g: "sector", s: true });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/rotation-map", {
      params: { query: { asof: "2026-06-01", group_by: "sector", include_silent: true } },
    });
  });
});
