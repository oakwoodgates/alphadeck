import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The transport, mocked. The REAL hooks run: the steward's close and reopen of a user's flag, and the paged closed list.
const h = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get, POST: h.post } }));

import { useClosedFlagPages, useCloseFlag, useCompanyValues, useReopenFlag } from "../hooks";

const SID = "00000000-0000-4000-8000-0000000000aa";
const FID = "a0000000-0000-4000-8000-000000000001";
const DID = "d0000000-0000-4000-8000-000000000001";

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

const answer = { security_id: SID, ticker: "ACME", flags: { open: [], closed: [], closed_total: 0 } };

beforeEach(() => {
  h.get.mockReset();
  h.post.mockReset();
  h.post.mockResolvedValue({ data: answer, error: undefined });
});

describe("useCloseFlag / useReopenFlag", () => {
  it("close posts the outcome, the trimmed note and the decision; the answer replaces the company read; the queue and user reads are refreshed", async () => {
    const { qc, wrapper } = setup();
    qc.setQueryData(["admin-platform-values", SID], { stale: true });
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useCloseFlag(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: SID, flagId: FID, outcome: "fixed", note: "  done ", decisionId: DID });
    });
    expect(h.post).toHaveBeenCalledWith("/admin/platform-values/flags/{flag_id}/close", {
      params: { path: { flag_id: FID } },
      body: { outcome: "fixed", note: "done", decision_id: DID },
    });
    expect(qc.getQueryData(["admin-platform-values", SID])).toEqual(answer);
    const keys = spy.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown[] }).queryKey));
    expect(keys).toContain(JSON.stringify(["admin-platform-queue"]));
    expect(keys).toContain(JSON.stringify(["platform-values"]));
    expect(keys).toContain(JSON.stringify(["admin-platform-values", SID, "closed-flags"]));
  });

  it("close of a kept flag sends no decision", async () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useCloseFlag(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: SID, flagId: FID, outcome: "kept", note: "stays" });
    });
    expect(h.post.mock.calls[0][1].body).toEqual({ outcome: "kept", note: "stays" });
  });

  it("a refusal is thrown as the server's body and re-reads the company", async () => {
    h.post.mockResolvedValue({ data: undefined, error: { detail: "This flag is already closed." } });
    const { qc, wrapper } = setup();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useCloseFlag(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: SID, flagId: FID, outcome: "kept", note: "x" }).catch(() => undefined);
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toEqual({ detail: "This flag is already closed." });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["admin-platform-values", SID] });
  });

  it("reopen posts with no body and replaces the company read", async () => {
    const { qc, wrapper } = setup();
    const { result } = renderHook(() => useReopenFlag(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: SID, flagId: FID });
    });
    expect(h.post).toHaveBeenCalledWith("/admin/platform-values/flags/{flag_id}/reopen", {
      params: { path: { flag_id: FID } },
    });
    expect(qc.getQueryData(["admin-platform-values", SID])).toEqual(answer);
  });
});

describe("useClosedFlagPages", () => {
  it("reads nothing for page 0 (the company read carries it) and one page per step with flags_offset / flags_limit", async () => {
    h.get.mockResolvedValue({ data: { flags: { closed: [{ id: "x1" }] } }, error: undefined });
    const { wrapper } = setup();
    const { result, rerender } = renderHook(({ n }) => useClosedFlagPages(SID, n, 20), {
      wrapper,
      initialProps: { n: 0 },
    });
    expect(h.get).not.toHaveBeenCalled();
    rerender({ n: 1 });
    await waitFor(() => expect(result.current.rows).toEqual([{ id: "x1" }]));
    expect(h.get).toHaveBeenCalledWith("/admin/platform-values/{security_id}", {
      params: { path: { security_id: SID }, query: { flags_offset: 20, flags_limit: 20 } },
    });
  });

  it("does not disturb the company read's own key", async () => {
    h.get.mockResolvedValue({ data: answer, error: undefined });
    const { wrapper } = setup();
    const { result } = renderHook(() => useCompanyValues(SID), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledWith("/admin/platform-values/{security_id}", {
      params: { path: { security_id: SID } },
    });
  });
});
