import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// F1-5 — the two writes behind the Splits panel's "Split filings not placed" section: the steward's Dismiss of an
// unplaced SEC filing ("not a split to list") and its Undo. Each is fired only by a click (never retried), posts the
// pinned route, and refreshes the split list and the status through useSplitAction. Display only: the same
// invalidations as every split action, nothing else.
const h = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("../client", () => ({ api: { POST: h.post, GET: vi.fn() } }));

import { useDismissUnplaced, useUndoUnplacedDismissal } from "../hooks";

const SECURITY = "9b1d0000-0000-4000-8000-0000000000b1";
const ACCESSION = "0001493152-26-052687";
const DISMISSAL = "9b1d0000-0000-4000-8000-0000000000d1";

function harness() {
  const qc = new QueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  const keys = () => spy.mock.calls.map((c) => JSON.stringify(c[0]?.queryKey));
  return { wrapper, keys };
}

beforeEach(() => {
  h.post.mockReset();
  h.post.mockResolvedValue({ data: { accession: ACCESSION }, error: null });
});

describe("useDismissUnplaced", () => {
  it("posts the filing's dismiss route with the security and accession as path parameters and the trimmed reason", async () => {
    const { wrapper } = harness();
    const { result } = renderHook(() => useDismissUnplaced(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: SECURITY, accession: ACCESSION, reason: "  covered by the 11-30 split " });
    });
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.post).toHaveBeenCalledWith("/admin/splits/unplaced/{security_id}/{accession}/dismiss", {
      params: { path: { security_id: SECURITY, accession: ACCESSION } },
      body: { reason: "covered by the 11-30 split" },
    });
  });

  it("sends an empty body when no reason is given (the server stores its default)", async () => {
    const { wrapper } = harness();
    const { result } = renderHook(() => useDismissUnplaced(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: SECURITY, accession: ACCESSION });
      await result.current.mutateAsync({ securityId: SECURITY, accession: ACCESSION, reason: "   " });
    });
    for (const call of h.post.mock.calls) expect(call[1].body).toEqual({});
  });

  it("refreshes the split list and the status once it is done", async () => {
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useDismissUnplaced(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: SECURITY, accession: ACCESSION });
    });
    expect(keys()).toContain(JSON.stringify(["admin-splits"]));
    expect(keys()).toContain(JSON.stringify(["admin-status"]));
  });

  it("throws the server's refusal, invalidates nothing and is not retried", async () => {
    h.post.mockResolvedValue({ data: undefined, error: { detail: "filing is already dismissed" } });
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useDismissUnplaced(), { wrapper });
    await act(async () => {
      await expect(result.current.mutateAsync({ securityId: SECURITY, accession: ACCESSION })).rejects.toEqual({
        detail: "filing is already dismissed",
      });
    });
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(keys()).toEqual([]);
  });
});

describe("useUndoUnplacedDismissal", () => {
  it("posts the dismissal's undo route with an empty body", async () => {
    const { wrapper } = harness();
    const { result } = renderHook(() => useUndoUnplacedDismissal(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ dismissalId: DISMISSAL });
    });
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.post).toHaveBeenCalledWith("/admin/splits/unplaced/dismissals/{dismissal_id}/undo", {
      params: { path: { dismissal_id: DISMISSAL } },
      body: {},
    });
  });

  it("refreshes the split list and the status once it is done", async () => {
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useUndoUnplacedDismissal(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ dismissalId: DISMISSAL });
    });
    expect(keys()).toContain(JSON.stringify(["admin-splits"]));
    expect(keys()).toContain(JSON.stringify(["admin-status"]));
  });
});
