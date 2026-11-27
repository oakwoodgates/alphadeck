import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// F2-2 — the two calls behind the Splits panel's "Add this split...": the write that lists a split no source lists
// (POST /admin/splits, fired only by a click, never retried, undone by Revoke) and the stored tape's step read for a
// SECURITY (the same reading as a version's, keyed by the security because no version exists yet).
const h = vi.hoisted(() => ({ post: vi.fn(), get: vi.fn() }));
vi.mock("../client", () => ({ api: { POST: h.post, GET: h.get } }));

import { useAddSplit, useSplitTapeStep } from "../hooks";

const SECURITY = "9b1d0000-0000-4000-8000-0000000000b1";
const VERSION = "9b1d0000-0000-4000-8000-0000000000a1";
const BODY = {
  security_id: SECURITY,
  ratio_from: "20",
  ratio_to: "1",
  first_session: "2026-11-30",
  cited_url: "https://www.sec.gov/Archives/edgar/data/1/000149315226052687/0001493152-26-052687-index.htm",
  from_accession: "0001493152-26-052687",
};

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
  h.get.mockReset();
  h.post.mockResolvedValue({ data: { version_id: VERSION, status: "confirmed" }, error: null });
  h.get.mockResolvedValue({ data: { first_session: "2026-11-30", step: 18.4 }, error: null });
});

describe("useAddSplit", () => {
  it("posts the body as given to /admin/splits, once", async () => {
    const { wrapper } = harness();
    const { result } = renderHook(() => useAddSplit(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync(BODY);
    });
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(h.post).toHaveBeenCalledWith("/admin/splits", { body: BODY });
  });

  it("sends the jump's provenance and the override as they are given, nothing added", async () => {
    const { wrapper } = harness();
    const { result } = renderHook(() => useAddSplit(), { wrapper });
    const fromJump = {
      security_id: SECURITY,
      ratio_from: "8",
      ratio_to: "1",
      first_session: "2026-08-24",
      cited_url: "https://example.com/notice",
      note: "8-K and the exchange notice",
      override_tape: true,
      from_jump: "2026-08-24",
    };
    await act(async () => {
      await result.current.mutateAsync(fromJump);
    });
    const sent = h.post.mock.calls[0][1].body;
    expect(sent).toEqual(fromJump);
    expect(sent).not.toHaveProperty("from_accession");
  });

  it("returns the listed split, and refreshes the split list, the status and the dashboards it moves", async () => {
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useAddSplit(), { wrapper });
    let out: unknown;
    await act(async () => {
      out = await result.current.mutateAsync(BODY);
    });
    expect(out).toEqual({ version_id: VERSION, status: "confirmed" });
    expect(keys()).toContain(JSON.stringify(["admin-splits"]));
    expect(keys()).toContain(JSON.stringify(["admin-status"]));
    expect(keys()).toContain(JSON.stringify(["data-flags"]));
  });

  it("throws the server's refusal (a 409 sentence), invalidates nothing and is not retried", async () => {
    const refusal = { detail: "a live 1-for-20 reverse split is listed within 45 days of 2026-11-30" };
    h.post.mockResolvedValue({ data: undefined, error: refusal });
    const { wrapper, keys } = harness();
    const { result } = renderHook(() => useAddSplit(), { wrapper });
    await act(async () => {
      await expect(result.current.mutateAsync(BODY)).rejects.toEqual(refusal);
    });
    expect(h.post).toHaveBeenCalledTimes(1);
    expect(keys()).toEqual([]);
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.variables).toEqual(BODY); // the form that sent it can tell the refusal is its own
  });
});

describe("useSplitTapeStep", () => {
  it("reads a version's step from the version's route, as before", async () => {
    const { wrapper } = harness();
    const args = { firstSession: "2026-09-24", ratioFrom: "10", ratioTo: "3" };
    const { result } = renderHook(() => useSplitTapeStep({ versionId: VERSION }, args), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(h.get).toHaveBeenCalledTimes(1);
    expect(h.get).toHaveBeenCalledWith("/admin/splits/{version_id}/tape-step", {
      params: { path: { version_id: VERSION }, query: { first_session: "2026-09-24", ratio_from: "10", ratio_to: "3" } },
    });
  });

  it("reads a security's step from the security's route, the same query", async () => {
    const { wrapper } = harness();
    const args = { firstSession: "2026-11-30", ratioFrom: "20", ratioTo: "1" };
    const { result } = renderHook(() => useSplitTapeStep({ securityId: SECURITY }, args), { wrapper });
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(h.get).toHaveBeenCalledTimes(1);
    expect(h.get).toHaveBeenCalledWith("/admin/splits/securities/{security_id}/tape-step", {
      params: { path: { security_id: SECURITY }, query: { first_session: "2026-11-30", ratio_from: "20", ratio_to: "1" } },
    });
  });

  it("asks for the step alone while no ratio is entered", async () => {
    const { wrapper } = harness();
    const { result } = renderHook(() => useSplitTapeStep({ securityId: SECURITY }, { firstSession: "2026-08-24" }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.data).toBeDefined());
    const query = h.get.mock.calls[0][1].params.query;
    expect(query.first_session).toBe("2026-08-24");
    expect(query.ratio_from).toBeUndefined();
    expect(query.ratio_to).toBeUndefined();
  });

  it("reads nothing until a first session is entered, for either target", async () => {
    const { wrapper } = harness();
    const a = renderHook(() => useSplitTapeStep({ securityId: SECURITY }, null), { wrapper });
    const b = renderHook(() => useSplitTapeStep({ versionId: VERSION }, null), { wrapper });
    await act(async () => {
      await Promise.resolve();
    });
    expect(h.get).not.toHaveBeenCalled();
    expect(a.result.current.data).toBeUndefined();
    expect(b.result.current.data).toBeUndefined();
  });

  it("keeps a security's read and a version's read apart in the cache", async () => {
    const { wrapper } = harness();
    const args = { firstSession: "2026-11-30" };
    // the same id text on both sides must not share an answer
    renderHook(() => useSplitTapeStep({ securityId: SECURITY }, args), { wrapper });
    renderHook(() => useSplitTapeStep({ versionId: SECURITY }, args), { wrapper });
    await waitFor(() => expect(h.get).toHaveBeenCalledTimes(2));
    const paths = h.get.mock.calls.map((c) => c[0]).sort();
    expect(paths).toEqual([
      "/admin/splits/securities/{security_id}/tape-step",
      "/admin/splits/{version_id}/tape-step",
    ]);
  });

  it("throws the server's refusal and is not retried", async () => {
    h.get.mockResolvedValue({ data: undefined, error: { detail: "no security 9b1d" } });
    const { wrapper } = harness();
    const { result } = renderHook(() => useSplitTapeStep({ securityId: SECURITY }, { firstSession: "2026-11-30" }), {
      wrapper,
    });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(h.get).toHaveBeenCalledTimes(1);
  });
});
