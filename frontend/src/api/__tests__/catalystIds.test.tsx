import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// V2 W6b: the catalyst list keeps its row ids across saves. A counting entry is keyed by its calendar row's id,
// so the SPAC Radar's read-modify-PUT must send back every id it read (a row sent without one is a NEW row, and
// the server refuses to drop a row whose entry still counts).
const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), put: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get, POST: h.post, PUT: h.put } }));

import { useSpacCatalyst } from "../hooks";

function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  h.get.mockReset();
  h.put.mockReset();
  h.put.mockResolvedValue({ data: {}, error: null });
  h.get.mockResolvedValue({
    data: {
      catalysts: [
        { id: "c1", label: "NRC decision", kind: "regulatory", when_date: "2026-12-01", when_label: null },
        { id: "c2", label: "DOE loan", kind: null, when_date: null, when_label: "H2" },
      ],
    },
    error: null,
  });
});

describe("useSpacCatalyst keeps the list's ids", () => {
  it("re-sends every existing row WITH its id and adds the vote row without one", async () => {
    const { result } = renderHook(() => useSpacCatalyst(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ thesisId: "t1", label: "SPAC vote: ABCD" });
    });
    expect(h.put).toHaveBeenCalledTimes(1);
    expect(h.put.mock.calls[0][1].body).toEqual([
      { id: "c1", label: "NRC decision", kind: "regulatory", when_date: "2026-12-01", when_label: null },
      { id: "c2", label: "DOE loan", kind: null, when_date: null, when_label: "H2" },
      { label: "SPAC vote: ABCD", kind: "spac", when_date: null, when_label: "TBD" },
    ]);
    expect(h.put.mock.calls[0][1].body[2]).not.toHaveProperty("id");
  });

  it("writes nothing when the vote row is already on the list", async () => {
    const { result } = renderHook(() => useSpacCatalyst(), { wrapper });
    let out: unknown;
    await act(async () => {
      out = await result.current.mutateAsync({ thesisId: "t1", label: "DOE loan" });
    });
    expect(out).toEqual({ already: true });
    expect(h.put).not.toHaveBeenCalled();
  });
});
