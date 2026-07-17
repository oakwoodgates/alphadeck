import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// S4 — the identity-mismatch surfaces (the misbind class, KLAC↔LRCX / SIMO↔MXL). The basket stores the
// member's LABEL; the scored read joins the BOUND master ticker by security_id. A disagreement renders a
// loud ⚠ chip (rare by design — pre-guard damage or a deliberate override), and a promote 422 for an
// identity mismatch offers the explicit bind-anyway override (per-promote, logged server-side).
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const basket = [
    // the misbind: stored label SIMO, but the id is MaxLinear's row (bound ticker MXL below)
    { ticker: "SIMO", role: "r", archetype: "leader", security_id: "s-mxl", segment: null, authored_by: "system_drafted", thesis_fit: null },
    // a clean member: label agrees with the bound row
    { ticker: "KLAC", role: "r", archetype: "shovel", security_id: "s-klac", segment: null, authored_by: "operator_set", thesis_fit: null },
  ];
  const members = [
    { security_id: "s-mxl", ticker: "MXL", name: "MAXLINEAR, INC", archetype: "leader", archetype_hint: null, segment: null, purity: fig(null, null), runway: fig(null, null), catalysts: fig(null, null), dilution: fig(null, null), market_cap: fig(null, null), fit: "" },
    { security_id: "s-klac", ticker: "KLAC", name: "KLA CORP", archetype: "shovel", archetype_hint: null, segment: null, purity: fig(null, null), runway: fig(null, null), catalysts: fig(null, null), dilution: fig(null, null), market_cap: fig(null, null), fit: "" },
  ];
  const thesis = {
    id: "t-semis",
    name: "Semis",
    narrative: "n",
    ticker: null,
    segments: [],
    basket,
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
  };
  return { thesis, scored: { thesis_id: "t-semis", asof: "2026-07-13", segments: [], members } };
});

const h = vi.hoisted(() => ({
  mutate: vi.fn(),
  state: { isError: false, error: null as unknown },
}));

vi.mock("../../api/hooks", () => ({
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-semis", name: "Semis", ticker: null, basket_size: 2, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: h.mutate,
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    isError: h.state.isError,
    isSuccess: false,
    error: h.state.error,
  }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  // the AUTO-shares auto-confirm fired by get-data (inert here; its own suite covers it)
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useDraftChain: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () =>
  render(<Workbench asof="2026-07-13" onAsofChange={() => {}} onBack={() => {}} />);

describe("Workbench — identity-mismatch chip + the bind-anyway override (S4)", () => {
  beforeEach(() => {
    h.mutate.mockReset();
    h.state.isError = false;
    h.state.error = null;
  });

  it("flags ONLY the member whose stored label disagrees with its bound row — both identities named", () => {
    renderWb();
    const chip = screen.getByText(/label SIMO ≠ bound MXL/);
    expect(chip).toBeInTheDocument();
    expect(chip.getAttribute("title")).toMatch(/bound to MXL/); // the why + the way out, on hover
    expect(chip.getAttribute("title")).toMatch(/Re-pick|bind-anyway/i);
    expect(screen.queryByText(/label KLAC/)).not.toBeInTheDocument(); // the clean member stays quiet
  });

  it("a plain promote sends NO overrides (an override is never ambient)", async () => {
    const user = userEvent.setup();
    renderWb();
    await user.click(screen.getByRole("button", { name: /promote to thesis/i }));
    expect(h.mutate).toHaveBeenCalledTimes(1);
    expect(h.mutate.mock.calls[0][0].identity_overrides).toBeUndefined();
  });

  it("after an identity-mismatch 422, bind-anyway re-sends the SAME chain listing the flagged ids", async () => {
    h.state.isError = true;
    h.state.error = { detail: "identity mismatch: 'SIMO' is bound to MXL (MAXLINEAR, INC, CIK 0001288469) — …" };
    const user = userEvent.setup();
    renderWb();
    const btn = screen.getByRole("button", { name: /bind anyway — accept 1 identity mismatch/i });
    await user.click(btn);
    expect(h.mutate).toHaveBeenCalledTimes(1);
    const payload = h.mutate.mock.calls[0][0];
    expect(payload.identity_overrides).toEqual(["s-mxl"]); // exactly the flagged member, nothing blanket
    expect(payload.basket).toHaveLength(2); // the chain rides verbatim (the wipe-trap discipline)
  });

  it("a non-identity promote error offers NO override (the hatch opens only for the misbind class)", () => {
    h.state.isError = true;
    h.state.error = { detail: "basket member 'SIMO' references a security not in this tenant's master" };
    renderWb();
    expect(screen.queryByRole("button", { name: /bind anyway/i })).not.toBeInTheDocument();
  });
});
