import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The Ticker Lookup's reads: the resolve GETs the typed ticker (a 404 is "no such company", not an error), the page
// waits for a resolved company and GETs it at the as-of with the company in the path. The filings window is client
// side, so it is not a request parameter.
const h = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../../../api/client", () => ({ api: { GET: h.get } }));

import { fetchResolve, resolveKey, useResolveTicker, useTickerLookup } from "../useTickerLookup";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => h.get.mockReset());

describe("fetchResolve", () => {
  it("GETs the typed ticker, with the former-ticker flag only when asked", async () => {
    h.get.mockResolvedValue({ data: { security_id: "s1", ticker: "TLA", name: "Tape A", matched: "ticker" }, error: undefined });
    expect(await fetchResolve("TLA")).toMatchObject({ security_id: "s1" });
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/ticker-lookup/resolve", {
      params: { query: { t: "TLA", former: false } },
    });
    await fetchResolve("OLDT", true);
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/ticker-lookup/resolve", {
      params: { query: { t: "OLDT", former: true } },
    });
  });

  it("reads a 404 as no company and throws any other failure", async () => {
    h.get.mockResolvedValue({ data: undefined, error: { detail: "no company" }, response: { status: 404 } });
    expect(await fetchResolve("NOPE")).toBeNull();
    h.get.mockResolvedValue({ data: undefined, error: { detail: "boom" }, response: { status: 500 } });
    await expect(fetchResolve("TLA")).rejects.toEqual({ detail: "boom" });
  });

  it("keys a resolve by the upper-cased ticker and the flag", () => {
    expect(resolveKey("tla")).toEqual(["dashboard-ticker-lookup-resolve", "TLA", false]);
    expect(resolveKey("tla", true)).toEqual(["dashboard-ticker-lookup-resolve", "TLA", true]);
  });
});

describe("useResolveTicker", () => {
  it("waits for a ticker, then resolves it, and keeps a missing company as null", async () => {
    h.get.mockResolvedValue({ data: undefined, error: { detail: "no" }, response: { status: 404 } });
    const { result, rerender } = renderHook(({ t }: { t: string | null }) => useResolveTicker(t), {
      wrapper,
      initialProps: { t: null as string | null },
    });
    expect(h.get).not.toHaveBeenCalled();
    rerender({ t: "NOPE" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
    expect(h.get).toHaveBeenCalledTimes(1);
  });
});

describe("useTickerLookup", () => {
  it("waits for a company, then GETs it with the as-of and refetches on a change", async () => {
    h.get.mockResolvedValue({ data: { identity: {} }, error: undefined });
    const { result, rerender } = renderHook(
      ({ asof, id }: { asof: string; id: string | null }) => useTickerLookup(asof, id),
      { wrapper, initialProps: { asof: "2026-10-28", id: null as string | null } },
    );
    expect(h.get).not.toHaveBeenCalled();

    rerender({ asof: "2026-10-28", id: "s1" });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/ticker-lookup/{security_id}", {
      params: { path: { security_id: "s1" }, query: { asof: "2026-10-28" } },
    });

    rerender({ asof: "2026-06-05", id: "s1" });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    expect(h.get).toHaveBeenLastCalledWith("/dashboards/ticker-lookup/{security_id}", {
      params: { path: { security_id: "s1" }, query: { asof: "2026-06-05" } },
    });
  });
});
