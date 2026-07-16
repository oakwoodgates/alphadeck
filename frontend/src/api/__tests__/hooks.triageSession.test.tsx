import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// useTriageSession's error handling is load-bearing: a 404 must NOT hard-block the editor (a 404 can never
// mask a real saved session — that's always 200), but a 5xx/network MUST surface as isError (it CAN hide an
// existing prune → the retry gate, fix #1). We mock the transport to return each status.
const h = vi.hoisted(() => ({
  get: vi.fn(),
}));
vi.mock("../client", () => ({ api: { GET: h.get } }));

import { useTriageSession } from "../hooks";

function freshWrapper() {
  const qc = new QueryClient();
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => h.get.mockReset());

describe("useTriageSession error handling (deploy-skew resilience + fix #1)", () => {
  it("treats a 404 as 'no session' (mount fresh), not an error", async () => {
    // openapi-fetch surfaces a non-2xx as { error, response.status } with data undefined
    h.get.mockResolvedValue({ data: undefined, error: { detail: "Not Found" }, response: { status: 404 } });
    const { result } = renderHook(() => useTriageSession("t-1", true), { wrapper: freshWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.isError).toBe(false);
    expect(result.current.data).toEqual({ session: null }); // → the Workbench mounts fresh
  });

  it("surfaces a 500 as isError (a transient fault CAN hide a real prune → keep blocking + retry)", async () => {
    h.get.mockResolvedValue({ data: undefined, error: { detail: "boom" }, response: { status: 500 } });
    const { result } = renderHook(() => useTriageSession("t-1", true), { wrapper: freshWrapper() });
    await waitFor(() => expect(result.current.isError).toBe(true));
  });

  it("returns the session envelope on a 200", async () => {
    const env = { thesis_id: "t-1", schema_version: 1, updated_at: "now", state: { hook: {}, editor: {} } };
    h.get.mockResolvedValue({ data: { session: env }, error: undefined, response: { status: 200 } });
    const { result } = renderHook(() => useTriageSession("t-1", true), { wrapper: freshWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(result.current.data?.session).toEqual(env);
  });
});
