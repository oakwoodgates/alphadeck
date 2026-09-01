import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The transport, mocked: the draft-chain kick-off POST. The REAL useStartDraft runs — the assertions are on
// the WIRE SHAPE (draft-scope PR-2): the full draft must keep posting NO body (byte-identical to the
// pre-scope kick-off — backward-shape preservation is the requirement), and only the explicit fast lane
// sends {"scope":"seeds_only"} as the body.
const h = vi.hoisted(() => ({
  post: vi.fn(async () => ({ data: { job_id: "j1", status: "running" }, error: null })),
}));
vi.mock("../client", () => ({ api: { POST: h.post } }));

import { useStartDraft } from "../hooks";

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

beforeEach(() => h.post.mockClear());

describe("useStartDraft — the kick-off wire shape (draft scope)", () => {
  it("no scope → POSTs with NO body key at all (the full draft's pre-scope shape, preserved)", async () => {
    const { result } = renderHook(() => useStartDraft("t1"), { wrapper });
    result.current.mutate(undefined);

    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    const [path, init] = h.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/workbench/theses/{thesis_id}/draft-chain");
    expect(init.params).toEqual({ path: { thesis_id: "t1" } });
    expect("body" in init).toBe(false); // no body KEY — not even body: undefined rides the init
  });

  it('scope "seeds_only" → the kick-off body is exactly {"scope":"seeds_only"}', async () => {
    const { result } = renderHook(() => useStartDraft("t1"), { wrapper });
    result.current.mutate({ scope: "seeds_only" });

    await waitFor(() => expect(h.post).toHaveBeenCalledTimes(1));
    const [path, init] = h.post.mock.calls[0] as [string, Record<string, unknown>];
    expect(path).toBe("/workbench/theses/{thesis_id}/draft-chain");
    expect(init.body).toEqual({ scope: "seeds_only" });
  });
});
