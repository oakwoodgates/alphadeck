import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The transport, mocked. The REAL hooks run: the user's two reads of the shared values and the three flag routes.
const h = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
}));
vi.mock("../client", () => ({ api: { GET: h.get, POST: h.post } }));

import {
  useLogout,
  useMyFlags,
  useRaiseFlag,
  useUserCompanyValues,
  useUserThesisValues,
  useWithdrawFlag,
} from "../hooks";

const SID = "00000000-0000-0000-0000-0000000000a1";
const TID = "00000000-0000-0000-0000-0000000000b2";

function setup() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

beforeEach(() => {
  h.get.mockReset();
  h.post.mockReset();
  h.get.mockResolvedValue({ data: { members: [] }, error: undefined });
  h.post.mockResolvedValue({ data: { id: "f1" }, error: undefined });
});

describe("the user's reads of the shared values", () => {
  it("useUserCompanyValues reads GET /platform-values/{security_id}", async () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useUserCompanyValues(SID), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledWith("/platform-values/{security_id}", {
      params: { path: { security_id: SID } },
    });
  });

  it("useUserThesisValues reads GET /platform-values/theses/{thesis_id}", async () => {
    const { wrapper } = setup();
    const { result } = renderHook(() => useUserThesisValues(TID), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledWith("/platform-values/theses/{thesis_id}", {
      params: { path: { thesis_id: TID } },
    });
  });

  it("neither fires without an id", () => {
    const { wrapper } = setup();
    renderHook(() => useUserCompanyValues(undefined), { wrapper });
    renderHook(() => useUserThesisValues(undefined), { wrapper });
    expect(h.get).not.toHaveBeenCalled();
  });

  it("an error body is thrown as it came (the server's sentence rides it)", async () => {
    h.get.mockResolvedValue({ data: undefined, error: { detail: "There is no such company." } });
    const { wrapper } = setup();
    const { result } = renderHook(() => useUserCompanyValues(SID), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toEqual({ detail: "There is no such company." });
  });
});

describe("the flag routes", () => {
  it("useRaiseFlag posts the body and re-reads the values and the user's flags", async () => {
    const { qc, wrapper } = setup();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useRaiseFlag(), { wrapper });
    result.current.mutate({ security_id: SID, value_type: "cash_burn", note: "wrong" });
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["platform-values"] }));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["value-flags"] });
    expect(h.post).toHaveBeenCalledWith("/value-flags", {
      body: { security_id: SID, value_type: "cash_burn", note: "wrong" },
    });
  });

  it("a refused raise still re-reads the values (a 409 means the state moved) and throws the body", async () => {
    h.post.mockResolvedValue({ data: undefined, error: { detail: "You already have an open flag on this value. The steward will look at it." } });
    const { qc, wrapper } = setup();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useRaiseFlag(), { wrapper });
    result.current.mutate({ security_id: SID, value_type: "shares_outstanding" });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.error).toEqual({
      detail: "You already have an open flag on this value. The steward will look at it.",
    });
    expect(spy).toHaveBeenCalledWith({ queryKey: ["platform-values"] });
  });

  it("useWithdrawFlag posts to the flag's withdraw route and re-reads", async () => {
    const { qc, wrapper } = setup();
    const spy = vi.spyOn(qc, "invalidateQueries");
    const { result } = renderHook(() => useWithdrawFlag(), { wrapper });
    result.current.mutate({ flagId: "f-9" });
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["platform-values"] }));
    expect(spy).toHaveBeenCalledWith({ queryKey: ["value-flags"] });
    expect(h.post).toHaveBeenCalledWith("/value-flags/{flag_id}/withdraw", {
      params: { path: { flag_id: "f-9" } },
    });
  });

  it("useMyFlags reads GET /value-flags (paged)", async () => {
    h.get.mockResolvedValue({ data: { items: [], total: 0 }, error: undefined });
    const { wrapper } = setup();
    const { result } = renderHook(() => useMyFlags(25, 50), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledWith("/value-flags", { params: { query: { limit: 25, offset: 50 } } });
  });
});

describe("sign-out clears the new reads with the rest of the cache", () => {
  it("useLogout drops the shared-value and flag keys", async () => {
    h.post.mockResolvedValue({ data: undefined, error: undefined });
    const { qc, wrapper } = setup();
    qc.setQueryData(["platform-values", "company", SID], { shares: {} });
    qc.setQueryData(["platform-values", "thesis", TID], { members: [] });
    qc.setQueryData(["value-flags", 50, 0], { items: [] });
    const { result } = renderHook(() => useLogout(), { wrapper });
    result.current.mutate();
    await waitFor(() => expect(result.current.isSuccess || result.current.isError).toBe(true));
    expect(qc.getQueryData(["platform-values", "company", SID])).toBeUndefined();
    expect(qc.getQueryData(["platform-values", "thesis", TID])).toBeUndefined();
    expect(qc.getQueryData(["value-flags", 50, 0])).toBeUndefined();
  });
});
