import { QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// C3 — the Board's calls fire smallest-basket-first, at most CALL_CONCURRENCY in flight. This
// orders the PAINT; under one backend worker it cannot shorten the cold total (§8.4). The
// contract that matters most here is ALIGNMENT: the Board pairs callResults[i] with theses[i], so
// the gate must never reorder the returned array.
const h = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get } }));

import { CALL_CONCURRENCY, useCalls } from "../hooks";
import { createQueryClient } from "../queryClient";

const ASOF = "2026-09-02";

/** Requests started, in order, and a lever to settle each one on demand. */
let started: string[] = [];
let settle: Record<string, () => void> = {};

beforeEach(() => {
  started = [];
  settle = {};
  h.get.mockReset();
  h.get.mockImplementation((_path: string, opts: { params: { path: { thesis_id: string } } }) => {
    const id = opts.params.path.thesis_id;
    started.push(id);
    return new Promise((resolve) => {
      settle[id] = () => resolve({ data: { thesis_id: id, asof: ASOF }, error: null });
    });
  });
});

// deliberately NOT in size order, so a passing alignment assertion means something
const SUBJECTS = [
  { id: "big", basket_size: 196 },
  { id: "mid", basket_size: 90 },
  { id: "tiny", basket_size: 1 },
  { id: "zero", basket_size: 0 },
  { id: "small", basket_size: 4 },
  { id: "huge", basket_size: 200 },
];
// by basket size: zero(0) tiny(1) small(4) mid(90) big(196) huge(200)
const sortedIds = [...SUBJECTS]
  .sort((a, b) => a.basket_size - b.basket_size)
  .map((s) => s.id);

function mount(qc = createQueryClient(), subjects = SUBJECTS) {
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useCalls(subjects, ASOF), { wrapper });
}

const resolve = async (id: string) => {
  await act(async () => {
    settle[id]();
    await Promise.resolve();
  });
};

/** The set of ids started so far, order-insensitive — the three queries in a batch are fired in the
 *  same tick (useQueries walks its array), so WHICH three were selected is the contract; their
 *  order inside the batch is not observable to the operator. */
const startedSet = () => [...started].sort();

describe("useCalls — progressive paint (C3)", () => {
  it("fires at most CALL_CONCURRENCY at once, smallest basket first", async () => {
    mount();
    await waitFor(() => expect(started.length).toBe(CALL_CONCURRENCY));
    // the CALL_CONCURRENCY smallest baskets, and nothing else — the rest wait
    expect(startedSet()).toEqual(sortedIds.slice(0, CALL_CONCURRENCY).sort());

    // the next-smallest query must NOT start while the in-flight one is still pending — only
    // resolving it opens the slot. Walk the whole size-ordered queue one resolve at a time (at
    // CALL_CONCURRENCY=1 this is the literal staircase the paint produces).
    for (let next = CALL_CONCURRENCY; next < sortedIds.length; next++) {
      await act(async () => {
        await Promise.resolve(); // let any queued microtasks settle — nothing new should start
      });
      expect(started.length).toBe(next); // still waiting on the in-flight one(s)

      await resolve(sortedIds[next - CALL_CONCURRENCY]); // frees exactly one slot
      await waitFor(() => expect(started.length).toBe(next + 1));
      expect(started[next]).toBe(sortedIds[next]); // the freed slot goes to the next-smallest
    }
    // drain whatever is still in flight so nothing dangles between tests
    for (const id of sortedIds.slice(sortedIds.length - CALL_CONCURRENCY)) await resolve(id);

    // every thesis eventually ran, and the launch sequence after the first batch is size-ordered
    expect(startedSet()).toEqual([...sortedIds].sort());
  });

  it("returns results ALIGNED to the input order, not the launch order", async () => {
    const { result } = mount();
    // resolve strictly in launch (size) order — with CALL_CONCURRENCY=1 that's the only order
    // possible, but the assertion below is what actually matters: alignment to INPUT order.
    for (const id of sortedIds) {
      await waitFor(() => expect(started).toContain(id));
      await resolve(id);
    }

    await waitFor(() => expect(result.current.every((r) => r.isSuccess)).toBe(true));
    expect(result.current.map((r) => r.data?.thesis_id)).toEqual(SUBJECTS.map((s) => s.id));
  });

  it("an already-cached call renders immediately and occupies NO slot", async () => {
    const qc = createQueryClient();
    // the smallest basket is already in cache (the Board → Cockpit → Back path)
    qc.setQueryData(["call", "zero", ASOF], { thesis_id: "zero", asof: ASOF });

    const { result } = mount(qc);
    await waitFor(() => expect(started.length).toBe(CALL_CONCURRENCY));
    // the cached one never re-fetches, so its slot(s) go to the next-smallest not-yet-started
    // theses instead — "zero" is excluded from the queue entirely
    expect(startedSet()).toEqual(
      sortedIds.filter((id) => id !== "zero").slice(0, CALL_CONCURRENCY).sort(),
    );
    expect(result.current[3].data?.thesis_id).toBe("zero"); // index 3 = "zero" in input order
  });

  it("an errored call frees its slot (a retry-exhausted thesis never blocks the queue)", async () => {
    h.get.mockReset();
    h.get.mockImplementation((_path: string, opts: { params: { path: { thesis_id: string } } }) => {
      const id = opts.params.path.thesis_id;
      started.push(id);
      if (id === "zero") return Promise.resolve({ data: undefined, error: { detail: "boom" } });
      return new Promise((resolve) => {
        settle[id] = () => resolve({ data: { thesis_id: id, asof: ASOF }, error: null });
      });
    });

    mount();
    // "zero" is in the initial batch and fails (1 retry -> 2 attempts); its slot then opens for
    // the next-smallest thesis that hadn't started yet — sortedIds[CALL_CONCURRENCY]
    const nextSmallest = sortedIds[CALL_CONCURRENCY];
    await waitFor(() => expect(started.filter((id) => id === nextSmallest).length).toBe(1), {
      timeout: 5000,
    });
  });
});
