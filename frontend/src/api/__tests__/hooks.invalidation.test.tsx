import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// PR-1a's invalidation audit. A 10-minute staleTime is only honest if EVERY write that can change a
// MONITOR read invalidates it — otherwise the operator's own action would sit behind a stale cache.
// One test per addition; the keys are the contract.
const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get, POST: h.post } }));

import {
  useDailyRunJob,
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
