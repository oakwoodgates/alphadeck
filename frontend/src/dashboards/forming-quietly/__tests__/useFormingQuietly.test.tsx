import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Forming Quietly's one read: the as-of is the only request parameter (the Incubating toggle, the filters and the
// sorts are client side).
const h = vi.hoisted(() => ({
  get: vi.fn(async () => ({ data: { rows: [] }, error: null as unknown })),
}));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { useFormingQuietly } from "../useFormingQuietly";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => h.get.mockClear());

describe("useFormingQuietly", () => {
  it("GETs the page at the as-of and refetches when the as-of changes", async () => {
    const { result, rerender } = renderHook(({ asof }: { asof: string }) => useFormingQuietly(asof), {
      wrapper,
      initialProps: { asof: "2026-10-28" },
    });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/forming-quietly", { params: { query: { asof: "2026-10-28" } } });
    rerender({ asof: "2026-10-21" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/forming-quietly", { params: { query: { asof: "2026-10-21" } } });
  });

  it("throws the API's error so the page can say it could not load", async () => {
    h.get.mockResolvedValueOnce({ data: undefined as never, error: { detail: "boom" } });
    const { result } = renderHook(() => useFormingQuietly("2026-10-28"), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });
});
