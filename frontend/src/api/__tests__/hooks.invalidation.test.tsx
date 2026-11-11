import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// PR-1a's invalidation audit. A 10-minute staleTime is only honest if EVERY write that can change a
// MONITOR read invalidates it — otherwise the operator's own action would sit behind a stale cache.
// One test per addition; the keys are the contract.
const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get, POST: h.post, PUT: h.put } }));

import {
  invalidateDashboards,
  useAdoptPriceSymbol,
  useApplyAmendment,
  useAutoConfirmShares,
  useConfirmSplit,
  usePutCatalysts,
  usePutExclusions,
  usePutKillCriteria,
  useRatifyFact,
  useSetArchived,
  useSetMetaIndex,
  useSpacCatalyst,
  useThesisOnboarding,
  useDailyRunJob,
  useFundAttach,
  useIngestJobStatus,
  useIngestPrices,
  usePromoteThesis,
  useSectionData,
  useSetBusinessType,
  useSpacAttach,
} from "../hooks";

function harness() {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  /** every key this write invalidated, as comparable strings */
  const keys = () => spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
  return { qc, wrapper, keys };
}

const has = (keys: string[], key: unknown[]) => expect(keys).toContain(JSON.stringify(key));

beforeEach(() => {
  h.put.mockReset();
  h.put.mockResolvedValue({ data: [], error: null });
  h.get.mockReset();
  h.post.mockReset();
  h.post.mockResolvedValue({ data: { id: "t1" }, error: null });
  h.get.mockResolvedValue({ data: { facts: [] }, error: null });
});

describe("a basket edit re-derives the call and the tape", () => {
  it("usePromoteThesis invalidates call + display-signals for the thesis", async () => {
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => usePromoteThesis(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ name: "n", narrative: "x" } as never);
    });
    has(keys(), ["call", "t1"]);
    has(keys(), ["display-signals", "t1"]);
    has(keys(), ["theses"]); // the pre-existing ones still fire
    has(keys(), ["workbench-scored", "t1"]);
  });

  it("useSpacAttach invalidates call + display-signals for the attached thesis", async () => {
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useSpacAttach(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ thesis_id: "t9", cik: "0001" });
    });
    has(keys(), ["call", "t9"]);
    has(keys(), ["display-signals", "t9"]);
    has(keys(), ["radar-spac"]);
  });

  it("useFundAttach (add and remove) invalidates the call, display-signals and both fund reads", async () => {
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useFundAttach(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ thesis_id: "t9", symbol: "TSTX" });
      await result.current.mutateAsync({ thesis_id: "t9", symbol: "TSTX", detach: true });
    });
    expect(h.post.mock.calls.map((c) => c[0])).toEqual([
      "/radar/funds/attach",
      "/radar/funds/detach",
    ]);
    has(keys(), ["call", "t9"]);
    has(keys(), ["display-signals", "t9"]);
    has(keys(), ["radar-funds"]);
    has(keys(), ["fund-launches", "t9"]);
  });
});

describe("new bars re-derive everything that reads prices", () => {
  it("useIngestPrices invalidates call + display-signals (all theses, all as-ofs)", async () => {
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useIngestPrices(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("sec-1");
    });
    has(keys(), ["call"]);
    has(keys(), ["display-signals"]);
    has(keys(), ["workbench-scored"]);
  });

  it("useSectionData's post-run re-derive covers the call + the tape too", async () => {
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useSectionData("t1"), { wrapper });
    await act(async () => {
      await result.current.run([{ security_id: "sec-1", ticker: "AAA" }]);
    });
    has(keys(), ["call"]);
    has(keys(), ["display-signals"]);
    has(keys(), ["workbench-scored"]);
  });
});

describe("a business-type re-tag moves the sector-RS grouping, not the call", () => {
  it("useSetBusinessType invalidates display-signals but NEVER the call (#4)", async () => {
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useSetBusinessType(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: "sec-1", businessType: "miner" });
    });
    has(keys(), ["display-signals"]);
    has(keys(), ["workbench-scored"]);
    // business type is DISPLAY identity — wiring it into the call would be a #4 violation
    expect(keys()).not.toContain(JSON.stringify(["call"]));
  });
});

describe("a background job that wrote facts invalidates on its terminal status", () => {
  it("useDailyRunJob: on done, theses + call + display-signals + scoreboard — exactly once", async () => {
    h.get.mockResolvedValue({ data: { job_id: "j1", status: "done" }, error: null });
    const { wrapper, keys } = harness();
    const { result, rerender } = renderHook(() => useDailyRunJob("j1"), { wrapper });
    await waitFor(() => expect(result.current.data?.status).toBe("done"));

    for (const key of [["theses"], ["call"], ["display-signals"], ["scoreboard"]]) has(keys(), key);
    const after = keys().length;
    rerender(); // the poll re-renders constantly — the invalidation must not re-fire
    rerender();
    expect(keys().length).toBe(after);
  });

  it("useDailyRunJob: a RUNNING job invalidates nothing", async () => {
    h.get.mockResolvedValue({ data: { job_id: "j2", status: "running" }, error: null });
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useDailyRunJob("j2"), { wrapper });
    await waitFor(() => expect(result.current.data?.status).toBe("running"));
    expect(keys()).toEqual([]);
  });

  it("useIngestJobStatus: on done, this thesis's call + tape + scored", async () => {
    h.get.mockResolvedValue({ data: { job_id: "j3", status: "done" }, error: null });
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useIngestJobStatus("t7", "j3"), { wrapper });
    await waitFor(() => expect(result.current.data?.status).toBe("done"));
    has(keys(), ["call", "t7"]);
    has(keys(), ["display-signals", "t7"]);
    has(keys(), ["workbench-scored", "t7"]);
  });
});

// Dashboard cache plan: every dashboard key starts with "dashboard-", and a write that can change what
// one shows must mark them stale (refetch if mounted) so the 10-minute window never hides the edit.
const DASH_KEY = ["dashboard-x", "2026-01-02"];
const OTHER_KEY = ["auth-me"];

function seeded(qc: QueryClient) {
  qc.setQueryData(DASH_KEY, { rows: [] });
  qc.setQueryData(["dashboard-y", "2026-01-02", "p"], { rows: [] });
  qc.setQueryData(OTHER_KEY, { me: 1 });
  return (key: unknown[]) => qc.getQueryState(key)?.isInvalidated === true;
}

type Fire = (vars?: unknown) => Promise<unknown>;

describe("invalidateDashboards", () => {
  it("marks every dashboard-* query stale and nothing else", () => {
    const { qc } = harness();
    const stale = seeded(qc);
    invalidateDashboards(qc);
    expect(stale(DASH_KEY)).toBe(true);
    expect(stale(["dashboard-y", "2026-01-02", "p"])).toBe(true);
    expect(stale(OTHER_KEY)).toBe(false);
  });
});

describe("a write that can change a dashboard marks dashboard queries stale", () => {
  // each hook, and the variables its mutation takes
  const cases: Array<[string, () => { mutateAsync: Fire }, unknown]> = [
    ["useSetArchived", () => useSetArchived(), { thesisId: "t1", archived: true }],
    ["useSetMetaIndex", () => useSetMetaIndex(), { thesisId: "t1", metaIndex: true }],
    ["usePromoteThesis", () => usePromoteThesis(), { name: "n", narrative: "x" }],
    ["useSpacAttach", () => useSpacAttach(), { thesis_id: "t9", cik: "0001" }],
    ["useFundAttach", () => useFundAttach(), { thesis_id: "t9", symbol: "TSTX" }],
    ["usePutCatalysts", () => usePutCatalysts("t1"), []],
    ["useSpacCatalyst", () => useSpacCatalyst(), { thesisId: "t1", label: "L" }],
    ["usePutExclusions", () => usePutExclusions("t1"), []],
    ["usePutKillCriteria", () => usePutKillCriteria("t1"), []],
    ["useIngestPrices", () => useIngestPrices(), "sec-1"],
    ["useSetBusinessType", () => useSetBusinessType(), { securityId: "sec-1", businessType: "miner" }],
    ["useRatifyFact", () => useRatifyFact(), {}],
    ["useAutoConfirmShares (applied)", () => useAutoConfirmShares(), "sec-1"],
    ["a price-feed repair (useAdoptPriceSymbol)", () => useAdoptPriceSymbol(), { securityId: "sec-1", symbol: "TSTX" }],
    ["an insider-amendment decision (useApplyAmendment)", () => useApplyAmendment(), { securityId: "sec-1", accession: "a-1" }],
    ["a split action (useConfirmSplit)", () => useConfirmSplit(), { versionId: "v1", source: "sec" }],
  ];
  for (const [name, hook, vars] of cases) {
    it(`${name}: dashboards invalidated, an unrelated key untouched`, async () => {
      h.post.mockResolvedValue({ data: { id: "t1", applied: true }, error: null });
      const { qc, wrapper } = harness();
      const stale = seeded(qc);
      const { result } = renderHook(hook, { wrapper });
      await act(async () => {
        await result.current.mutateAsync(vars);
      });
      expect(stale(DASH_KEY)).toBe(true);
      expect(stale(OTHER_KEY)).toBe(false);
    });
  }

  it("useAutoConfirmShares: a declined apply moved nothing, so dashboards stay fresh", async () => {
    h.post.mockResolvedValue({ data: { applied: false }, error: null });
    const { qc, wrapper } = harness();
    const stale = seeded(qc);
    const { result } = renderHook(() => useAutoConfirmShares(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync("sec-1");
    });
    expect(stale(DASH_KEY)).toBe(false);
  });

  it("useSectionData: the post-run re-derive marks dashboards stale", async () => {
    const { qc, wrapper } = harness();
    const stale = seeded(qc);
    const { result } = renderHook(() => useSectionData("t1"), { wrapper });
    await act(async () => {
      await result.current.run([{ security_id: "sec-1", ticker: "AAA" }]);
    });
    expect(stale(DASH_KEY)).toBe(true);
    expect(stale(OTHER_KEY)).toBe(false);
  });
});

describe("a finished background job marks dashboard queries stale, once", () => {
  const jobs: Array<[string, (id: string) => { data?: { status?: string } }]> = [
    ["useDailyRunJob", (id) => useDailyRunJob(id)],
    ["useIngestJobStatus", (id) => useIngestJobStatus("t7", id)],
  ];
  for (const [name, hook] of jobs) {
    it(`${name}: on done, dashboards invalidated once; not again on re-render`, async () => {
      h.get.mockResolvedValue({ data: { job_id: "j1", status: "done" }, error: null });
      const { qc, wrapper } = harness();
      const stale = seeded(qc);
      const spy = vi.spyOn(qc, "invalidateQueries");
      const { result, rerender } = renderHook(() => hook("j1"), { wrapper });
      await waitFor(() => expect(result.current.data?.status).toBe("done"));
      await waitFor(() => expect(stale(DASH_KEY)).toBe(true));
      expect(stale(OTHER_KEY)).toBe(false);
      const predicateCalls = () => spy.mock.calls.filter((c) => c[0]?.predicate).length;
      expect(predicateCalls()).toBe(1);
      rerender();
      rerender();
      expect(predicateCalls()).toBe(1);
    });
  }

  it("a RUNNING job leaves dashboards alone", async () => {
    h.get.mockResolvedValue({ data: { job_id: "j2", status: "running" }, error: null });
    const { qc, wrapper } = harness();
    const stale = seeded(qc);
    const { result } = renderHook(() => useDailyRunJob("j2"), { wrapper });
    await waitFor(() => expect(result.current.data?.status).toBe("running"));
    expect(stale(DASH_KEY)).toBe(false);
  });

  it("useThesisOnboarding: more names done marks dashboards stale", async () => {
    h.get.mockResolvedValue({ data: { counts: { done: 1, queued: 1 } }, error: null });
    const { qc, wrapper } = harness();
    const stale = seeded(qc);
    const { result } = renderHook(() => useThesisOnboarding("t1", true), { wrapper });
    await waitFor(() => expect(result.current.data?.counts?.done).toBe(1));
    // (the poller counts the first load as growth from 0, so a fresh query may already be stale here;
    // reset it to prove the NEXT growth does it)
    await act(async () => {
      await qc.resetQueries({ queryKey: DASH_KEY });
    });
    qc.setQueryData(DASH_KEY, { rows: [] });
    expect(stale(DASH_KEY)).toBe(false);
    h.get.mockResolvedValue({ data: { counts: { done: 2, queued: 0 } }, error: null });
    await act(async () => {
      await result.current.refetch();
    });
    await waitFor(() => expect(stale(DASH_KEY)).toBe(true));
    expect(stale(OTHER_KEY)).toBe(false);
  });
});
