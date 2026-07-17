import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The SECTION runner (gate 2 at section granularity): per member it pulls prices (POST ingest-prices)
// and prefetches the extract into the SHARED query — bounded by exactly the members passed, cache-first
// client-side (an already-cached extract is never re-spent), failures aggregated LOUDLY per name, and
// nothing here confirms a fact (extract-and-propose only).
const h = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
}));
vi.mock("../client", () => ({ api: { POST: h.post, GET: h.get } }));

import { extractQueryOptions, useSectionData } from "../hooks";

function wrapperWith(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

const M = (sid: string, ticker: string) => ({ security_id: sid, ticker });

beforeEach(() => {
  h.post.mockReset();
  h.get.mockReset();
  h.post.mockResolvedValue({
    data: { security_id: "x", ticker: "X", bars_appended: 2, latest_bar: "2026-07-08" },
    error: null,
  });
  h.get.mockResolvedValue({ data: [], error: null });
});

describe("useSectionData — the per-section prices + extraction runner", () => {
  it("runs BOTH legs for every passed member (and only those), then reports", async () => {
    const qc = new QueryClient();
    const { result } = renderHook(() => useSectionData("t1"), { wrapper: wrapperWith(qc) });
    await act(async () => {
      await result.current.run([M("s-1", "AAA"), M("s-2", "BBB")]);
    });
    expect(h.post).toHaveBeenCalledTimes(2); // one price pull per member — bounded by the section
    expect(h.get).toHaveBeenCalledTimes(2); // one extract prefetch per member
    expect(result.current.report).toEqual({
      total: 2,
      pricesOk: 2,
      extractsOk: 2,
      sharesAuto: 0, // these candidates carry no AUTO shares -> nothing auto-applied
      failures: [],
    });
  });

  it("auto-applies an AUTO shares count per member, sending NO value, and reports the count", async () => {
    const qc = new QueryClient();
    h.get.mockResolvedValue({
      data: [{ fact_type: "shares_outstanding", tier: "auto" }],
      error: null,
    });
    h.post.mockImplementation((path: string) =>
      path === "/workbench/facts/auto-confirm"
        ? Promise.resolve({ data: { applied: true, reason: "applied", fact_id: "f1" }, error: null })
        : Promise.resolve({ data: { bars_appended: 1 }, error: null }),
    );
    const { result } = renderHook(() => useSectionData("t1"), { wrapper: wrapperWith(qc) });
    await act(async () => {
      await result.current.run([M("s-1", "AAA"), M("s-2", "BBB")]);
    });

    const auto = h.post.mock.calls.filter(([p]) => p === "/workbench/facts/auto-confirm");
    expect(auto).toHaveLength(2); // one per member — the ceremonial confirm, removed
    // THE #3 BOUND, on the wire: the body names a security + fact type and carries NO number. The server
    // re-extracts and writes its own parse, so no client bug can inject a figure under `auto` provenance.
    expect(auto[0][1].body).toEqual({ security_id: "s-1", fact_type: "shares_outstanding" });
    expect(result.current.report?.sharesAuto).toBe(2); // surfaced, never a silent write
  });

  it("a FLAGged shares candidate is NEVER auto-applied — it stays the operator's to ratify", async () => {
    const qc = new QueryClient();
    h.get.mockResolvedValue({
      // dual-class / stale-cover: a judgment call the machine must not make
      data: [{ fact_type: "shares_outstanding", tier: "flag", flags: ["dual-class"] }],
      error: null,
    });
    const { result } = renderHook(() => useSectionData("t1"), { wrapper: wrapperWith(qc) });
    await act(async () => {
      await result.current.run([M("s-1", "AAA")]);
    });
    expect(h.post.mock.calls.filter(([p]) => p === "/workbench/facts/auto-confirm")).toHaveLength(0);
    expect(result.current.report?.sharesAuto).toBe(0);
  });

  it("an already-cached extract is NOT re-spent (cache-first client-side)", async () => {
    const qc = new QueryClient();
    // seed the SHARED query cache for s-1 (what a prior row-click / section run left behind)
    qc.setQueryData(extractQueryOptions("s-1", "t1").queryKey, []);
    const { result } = renderHook(() => useSectionData("t1"), { wrapper: wrapperWith(qc) });
    await act(async () => {
      await result.current.run([M("s-1", "AAA"), M("s-2", "BBB")]);
    });
    expect(h.get).toHaveBeenCalledTimes(1); // only s-2 fetched — s-1 came from the cache
    expect(h.post).toHaveBeenCalledTimes(2); // prices still run (incremental server-side, ~free)
    expect(result.current.report?.extractsOk).toBe(2); // a cached extract counts as staged
  });

  it("a failed leg lands in the report's failures, NAMED per ticker — never silent", async () => {
    h.post.mockImplementation(async (_url: string, opts: { params: { path: { security_id: string } } }) =>
      opts.params.path.security_id === "s-2"
        ? { data: null, error: { detail: "yahoo unreachable" } }
        : { data: { bars_appended: 1 }, error: null },
    );
    const qc = new QueryClient();
    const { result } = renderHook(() => useSectionData("t1"), { wrapper: wrapperWith(qc) });
    await act(async () => {
      await result.current.run([M("s-1", "AAA"), M("s-2", "BBB")]);
    });
    expect(result.current.report?.pricesOk).toBe(1);
    expect(result.current.report?.failures).toEqual([{ ticker: "BBB", what: "price" }]);
  });

  it("reset clears the report (it describes the LAST run's section)", async () => {
    const qc = new QueryClient();
    const { result } = renderHook(() => useSectionData("t1"), { wrapper: wrapperWith(qc) });
    await act(async () => {
      await result.current.run([M("s-1", "AAA")]);
    });
    expect(result.current.report).not.toBeNull();
    act(() => result.current.reset());
    expect(result.current.report).toBeNull();
  });
});
