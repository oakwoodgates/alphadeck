import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// SurfaceEtf (ETF Sleeve, Slice 1): the operator types an ETF ticker; on resolve it adds a `fund` sleeve
// member (the master row carries instrument_kind=etf — the sleeve's one marker) and pulls its price. The hooks are mocked — the resolve
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

import { MeProvider } from "../../auth/me";
import { SurfaceEtf } from "../SurfaceEtf";

const me = (role: "user" | "super_admin") => ({
  id: "u-1",
  email: "someone@example.test",
  display_name: "Someone Test",
  role,
  workspace_id: "00000000-0000-0000-0000-0000000000a1",
});

describe("SurfaceEtf — the surface-ETF flow", () => {
  beforeEach(() => {
    h.resolveMutate.mockReset();
    h.ingestMutate.mockReset();
  });

  it("resolves the ticker, adds the sleeve member, and pulls its price", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    render(<SurfaceEtf existingKeys={new Set()} onAdd={onAdd} />);

    await user.type(screen.getByLabelText(/surface ETF ticker/i), "lit");
    await user.click(screen.getByRole("button", { name: /surface ETF/i }));

    expect(h.resolveMutate).toHaveBeenCalledWith("LIT"); // upper-cased before resolve
    expect(onAdd).toHaveBeenCalledTimes(1);
    const [member, name] = onAdd.mock.calls[0];
    expect(member.role).toBe("ETF sleeve"); // the sleeve marker is the master row's instrument_kind
    expect(member.security_id).toBe("s-lit");
    // a fund lands in the fixed, silent ETFs link — never the Discovered floor, where it would vote
    expect(member.segment).toBe("ETFs");
    // S1 honest authorship: a hand-add is auto-SIGNED-OFF (the surfacing IS the endorsement) but its
    // description honestly stays a model draft (nothing typed) — never the retired operator_set
    expect(member.authored_by).toBe("system_drafted");
    expect(member.signed_off).toBe(true);
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

  // Surfacing an ETF writes the SHARED company master, so it is the steward's (security hardening PR 1a): a
  // plain user never sees the control (the API answers 403 anyway); a steward does.
  it("renders nothing for a signed-in plain user", () => {
    const { container } = render(
      <MeProvider value={me("user")}>
        <SurfaceEtf existingKeys={new Set()} onAdd={vi.fn()} />
      </MeProvider>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the control for a steward", () => {
    render(
      <MeProvider value={me("super_admin")}>
        <SurfaceEtf existingKeys={new Set()} onAdd={vi.fn()} />
      </MeProvider>,
    );
    expect(screen.getByLabelText(/surface ETF ticker/i)).toBeInTheDocument();
  });
});
