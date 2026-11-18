import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The record-start read (W8c): GET /record/start, one tiny value per workspace that changes once (when the first
// nightly call is recorded). The hook hands back the first night's date, or null before any nightly call.
const h = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../client", () => ({ api: { GET: h.get } }));

import { useRecordStart } from "../hooks";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

beforeEach(() => h.get.mockReset());

describe("useRecordStart", () => {
  it("GETs /record/start and returns the first night's date", async () => {
    h.get.mockResolvedValue({ data: { first_night: "2026-12-03" }, error: null });
    const { result } = renderHook(() => useRecordStart(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(h.get).toHaveBeenCalledWith("/record/start");
    expect(result.current.data).toBe("2026-12-03");
  });

  it("returns null when no nightly call has been recorded yet", async () => {
    h.get.mockResolvedValue({ data: { first_night: null }, error: null });
    const { result } = renderHook(() => useRecordStart(), { wrapper });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data).toBeNull();
  });

  it("surfaces a failed read as an error, with no data", async () => {
    h.get.mockResolvedValue({ data: undefined, error: { detail: "x" } });
    const { result } = renderHook(() => useRecordStart(), { wrapper });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.data).toBeUndefined();
  });
});
