import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// V2 W6d: the ONE hook for the counting route. The body is exactly the five keys of CatalystCountingIn (the server
// refuses an unknown key); the cache takes the server's answer; the card's call read is invalidated.
const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get, POST: h.post, PUT: h.put } }));

import { useCatalystCounting } from "../hooks";

const saved = { id: "t1", catalysts: [], catalyst_counting: [{ entry_id: "e1", counts: true }] };

beforeEach(() => {
  h.put.mockReset();
  h.put.mockResolvedValue({ data: saved, error: null });
});

describe("useCatalystCounting", () => {
  it("PUTs the five keys to the entry's counting path, then adopts the answer and re-reads the call", async () => {
    const qc = new QueryClient();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={qc}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useCatalystCounting("t1"), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({
        entryId: "e1",
        body: { counts: true, grade: "flip", security_ids: [], citation: "https://sec.gov/x", relevant_until: null },
      });
    });
    const [path, init] = h.put.mock.calls[0];
    expect(path).toBe("/theses/{thesis_id}/catalysts/{entry_id}/counting");
    expect(init.params.path).toEqual({ thesis_id: "t1", entry_id: "e1" });
    expect(Object.keys(init.body).sort()).toEqual(
      ["citation", "counts", "grade", "relevant_until", "security_ids"].sort(),
    );
    expect(qc.getQueryData(["thesis", "t1"])).toEqual(saved);
    expect(spy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey)).toContainEqual(["call", "t1"]);
  });

  it("throws the server's error body so the row can show its sentence", async () => {
    h.put.mockResolvedValue({ data: undefined, error: { detail: "Pick a grade." } });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
    );
    const { result } = renderHook(() => useCatalystCounting("t1"), { wrapper });
    await act(async () => {
      await expect(
        result.current.mutateAsync({
          entryId: "e1",
          body: { counts: true, grade: null, security_ids: [], citation: "x", relevant_until: null },
        }),
      ).rejects.toEqual({ detail: "Pick a grade." });
    });
  });
});
