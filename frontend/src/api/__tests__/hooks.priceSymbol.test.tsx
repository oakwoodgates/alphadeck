import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

// W4-3 — the Adopt write's body. A never-priced name's cross-class Adopt carries the steward's confirm
// (`confirm_class`); every other Adopt (a renamed name's, a same-class one) keeps the body it always sent.
const h = vi.hoisted(() => ({ post: vi.fn() }));
vi.mock("../client", () => ({ api: { POST: h.post } }));

import { errText } from "../../workbench/format";
import { useAdoptPriceSymbol } from "../hooks";

const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={new QueryClient()}>{children}</QueryClientProvider>
);

beforeEach(() => {
  h.post.mockReset();
  h.post.mockResolvedValue({ data: { id: "c1" }, error: null });
});

describe("useAdoptPriceSymbol — the request body", () => {
  it("sends only the symbol unless the steward confirmed the class", async () => {
    const { result } = renderHook(() => useAdoptPriceSymbol(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: "sec-1", symbol: "SDCO" });
    });
    expect(h.post).toHaveBeenCalledWith("/admin/price-feed/{security_id}/adopt", {
      params: { path: { security_id: "sec-1" } },
      body: { symbol: "SDCO" },
    });
    expect(Object.keys(h.post.mock.calls[0][1].body)).toEqual(["symbol"]);
  });

  it("sends confirm_class: true when the Adopt carries the confirm", async () => {
    const { result } = renderHook(() => useAdoptPriceSymbol(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ securityId: "sec-1", symbol: "TKAAF", confirmClass: true });
    });
    expect(h.post).toHaveBeenCalledWith("/admin/price-feed/{security_id}/adopt", {
      params: { path: { security_id: "sec-1" } },
      body: { symbol: "TKAAF", confirm_class: true },
    });
  });

  it("a refused adopt (409) throws the server's body: its detail is the message the panel shows", async () => {
    const detail = "TKAAF is a foreign ordinary share and TKAAY is an ADR: check the price basis";
    h.post.mockResolvedValue({ data: undefined, error: { detail } });
    const { result } = renderHook(() => useAdoptPriceSymbol(), { wrapper });
    let caught: unknown;
    await act(async () => {
      await result.current.mutateAsync({ securityId: "sec-1", symbol: "TKAAF" }).catch((e) => (caught = e));
    });
    expect(errText(caught)).toBe(detail);
  });
});
