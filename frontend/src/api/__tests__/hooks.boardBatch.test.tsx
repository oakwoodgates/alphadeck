import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get } }));

import { CALL_CONCURRENCY, useCalls } from "../hooks";
import { createQueryClient } from "../queryClient";

const ASOF = "2026-09-02";

type GetOpts = {
  params?: {
    path?: { thesis_id?: string };
    query?: { asof?: string; cached_only?: boolean };
  };
};

let batchRequests: Array<{ asof?: string; cached_only?: boolean }> = [];
let individualStarted: string[] = [];
let settleBatch: ((res: { data?: unknown; error?: unknown }) => void) | null = null;
let settleIndividual: Record<string, () => void> = {};

beforeEach(() => {
  batchRequests = [];
  individualStarted = [];
  settleBatch = null;
  settleIndividual = {};
  h.get.mockReset();
});

function mount(
  subjects: readonly { id: string; basket_size?: number | null }[],
  qc = createQueryClient(),
) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useCalls(subjects, ASOF, { hydrateBatch: true }), { wrapper });
}

const resolveIndividual = async (id: string) => {
  await act(async () => {
    settleIndividual[id]();
    await Promise.resolve();
  });
};

describe("useCalls — cache-first batch hydration", () => {
  it("full warm hit seeds all cards in 1 batch request and fires 0 individual /call requests", async () => {
    const subjects = [
      { id: "big", basket_size: 196 },
      { id: "tiny", basket_size: 1 },
      { id: "mid", basket_size: 90 },
    ];

    h.get.mockImplementation((path: string, opts: GetOpts) => {
      if (path === "/theses/calls") {
        batchRequests.push(opts.params?.query ?? {});
        return new Promise((resolve) => {
          settleBatch = resolve;
        });
      }
      const id = opts.params?.path?.thesis_id ?? "unknown";
      individualStarted.push(id);
      return Promise.resolve({ data: { thesis_id: id, asof: ASOF }, error: null });
    });

    const { result } = mount(subjects);

    await waitFor(() => expect(batchRequests.length).toBe(1));
    expect(batchRequests[0]).toEqual({ asof: ASOF, cached_only: true });
    // Individual /call requests are held while the batch probe is in flight
    expect(individualStarted).toEqual([]);

    await act(async () => {
      settleBatch?.({
        data: {
          asof: ASOF,
          // Deliberately returned out of input order to verify alignment
          calls: [
            { thesis_id: "tiny", asof: ASOF },
            { thesis_id: "mid", asof: ASOF },
            { thesis_id: "big", asof: ASOF },
          ],
          missing_ids: [],
        },
        error: null,
      });
      await Promise.resolve();
    });

    await waitFor(() => expect(result.current.every((r) => r.isSuccess)).toBe(true));
    expect(result.current.map((r) => r.data?.thesis_id)).toEqual(["big", "tiny", "mid"]);
    expect(individualStarted).toEqual([]);
    expect(h.get).toHaveBeenCalledTimes(1);
  });

  it("partial warm hit renders warm cards immediately and fetches only cold cards smallest-basket-first", async () => {
    const subjects = [
      { id: "big-cold", basket_size: 150 },
      { id: "small-warm", basket_size: 2 },
      { id: "small-cold", basket_size: 5 },
      { id: "mid-warm", basket_size: 40 },
    ];

    h.get.mockImplementation((path: string, opts: GetOpts) => {
      if (path === "/theses/calls") {
        batchRequests.push(opts.params?.query ?? {});
        return Promise.resolve({
          data: {
            asof: ASOF,
            calls: [
              { thesis_id: "small-warm", asof: ASOF },
              { thesis_id: "mid-warm", asof: ASOF },
            ],
            missing_ids: ["small-cold", "big-cold"],
          },
          error: null,
        });
      }
      const id = opts.params?.path?.thesis_id ?? "unknown";
      individualStarted.push(id);
      return new Promise((resolve) => {
        settleIndividual[id] = () => resolve({ data: { thesis_id: id, asof: ASOF }, error: null });
      });
    });

    const { result } = mount(subjects);

    // Warm cards appear without any individual /call requests; only the smallest cold card starts
    await waitFor(() => expect(result.current[1]?.data?.thesis_id).toBe("small-warm"));
    expect(result.current[3]?.data?.thesis_id).toBe("mid-warm");
    await waitFor(() => expect(individualStarted.length).toBe(CALL_CONCURRENCY));
    expect(individualStarted).toEqual(["small-cold"]);

    // Resolving small-cold opens the slot for big-cold
    await resolveIndividual("small-cold");
    await waitFor(() => expect(individualStarted).toEqual(["small-cold", "big-cold"]));

    await resolveIndividual("big-cold");
    await waitFor(() => expect(result.current.every((r) => r.isSuccess)).toBe(true));
    expect(result.current.map((r) => r.data?.thesis_id)).toEqual([
      "big-cold",
      "small-warm",
      "small-cold",
      "mid-warm",
    ]);
    // Warm cards never fired /theses/{id}/call
    expect(individualStarted).not.toContain("small-warm");
    expect(individualStarted).not.toContain("mid-warm");
  });

  it("fail-open fallback: batch error falls back seamlessly to smallest-basket-first progressive paint", async () => {
    const subjects = [
      { id: "big", basket_size: 100 },
      { id: "tiny", basket_size: 1 },
      { id: "mid", basket_size: 20 },
    ];

    h.get.mockImplementation((path: string, opts: GetOpts) => {
      if (path === "/theses/calls") {
        batchRequests.push(opts.params?.query ?? {});
        return Promise.resolve({ data: undefined, error: { detail: "batch unavailable" } });
      }
      const id = opts.params?.path?.thesis_id ?? "unknown";
      individualStarted.push(id);
      return new Promise((resolve) => {
        settleIndividual[id] = () => resolve({ data: { thesis_id: id, asof: ASOF }, error: null });
      });
    });

    const { result } = mount(subjects);

    await waitFor(() => expect(batchRequests.length).toBe(1));
    await waitFor(() => expect(individualStarted.length).toBe(CALL_CONCURRENCY));
    expect(individualStarted).toEqual(["tiny"]);

    await resolveIndividual("tiny");
    await waitFor(() => expect(individualStarted).toEqual(["tiny", "mid"]));

    await resolveIndividual("mid");
    await waitFor(() => expect(individualStarted).toEqual(["tiny", "mid", "big"]));

    await resolveIndividual("big");
    await waitFor(() => expect(result.current.every((r) => r.isSuccess)).toBe(true));
    expect(result.current.map((r) => r.data?.thesis_id)).toEqual(["big", "tiny", "mid"]);
  });

  it("skips batch probe completely (0 HTTP requests) when all cards are already settled in QueryClient", async () => {
    const subjects = [
      { id: "t1", basket_size: 10 },
      { id: "t2", basket_size: 20 },
    ];
    const qc = createQueryClient();
    qc.setQueryData(["call", "t1", ASOF], { thesis_id: "t1", asof: ASOF });
    qc.setQueryData(["call", "t2", ASOF], { thesis_id: "t2", asof: ASOF });

    h.get.mockImplementation(() => {
      throw new Error("should not be called when client cache is already settled");
    });

    const { result } = mount(subjects, qc);

    await waitFor(() => expect(result.current.every((r) => r.isSuccess)).toBe(true));
    expect(result.current.map((r) => r.data?.thesis_id)).toEqual(["t1", "t2"]);
    expect(h.get).not.toHaveBeenCalled();
  });
});
