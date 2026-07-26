import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// SurfaceEtf (ETF Sleeve, Slice 1): the operator types an ETF ticker; on resolve it adds a `fund` sleeve
// member (archetype set at surface-time) and pulls the sleeve's price. The hooks are mocked — the resolve
// mock invokes its onSuccess with a canned marked-'etf' match (the real hook passes onSuccess through).
const h = vi.hoisted(() => ({
  resolveMutate: vi.fn(),
  ingestMutate: vi.fn(),
  match: {
    security_id: "s-lit",
    ticker: "LIT",
    name: "Global X Lithium & Battery Tech ETF",
    cik: null,
    instrument_kind: "etf",
  },
}));

vi.mock("../../api/hooks", () => ({
  useResolveEtf: () => ({
    mutate: (ticker: string, opts?: { onSuccess?: (m: unknown) => void }) => {
      h.resolveMutate(ticker);
      opts?.onSuccess?.(h.match);
    },
    isPending: false,
    isError: false,
    error: null,
  }),
  useIngestPrices: () => ({ mutate: h.ingestMutate, isPending: false, isError: false, error: null }),
}));

import { SurfaceEtf } from "../SurfaceEtf";

describe("SurfaceEtf — the surface-ETF flow", () => {
  beforeEach(() => {
    h.resolveMutate.mockReset();
    h.ingestMutate.mockReset();
  });

  it("resolves the ticker, adds a `fund` member, and pulls the sleeve price", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<SurfaceEtf existingKeys={new Set()} onAdd={onAdd} />);

    await user.type(screen.getByLabelText(/surface ETF ticker/i), "lit");
    await user.click(screen.getByRole("button", { name: /surface ETF/i }));

    expect(h.resolveMutate).toHaveBeenCalledWith("LIT"); // upper-cased before resolve
    expect(onAdd).toHaveBeenCalledTimes(1);
    const [member, name] = onAdd.mock.calls[0];
    expect(member.archetype).toBe("fund"); // the sleeve archetype, set at surface-time
    expect(member.security_id).toBe("s-lit");
    expect(member.authored_by).toBe("operator_set");
    expect(name).toBe("Global X Lithium & Battery Tech ETF"); // the name bridge
    expect(h.ingestMutate).toHaveBeenCalledWith("s-lit"); // the sleeve shows its price
  });

  it("does not double-add a sleeve already in the basket (reversible via the row's own remove)", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<SurfaceEtf existingKeys={new Set(["s-lit"])} onAdd={onAdd} />);

    await user.type(screen.getByLabelText(/surface ETF ticker/i), "LIT");
    await user.click(screen.getByRole("button", { name: /surface ETF/i }));

    expect(h.resolveMutate).toHaveBeenCalled(); // it resolves...
    expect(onAdd).not.toHaveBeenCalled(); // ...but never re-adds an existing member
    expect(h.ingestMutate).not.toHaveBeenCalled();
  });
});
