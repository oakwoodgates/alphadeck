import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// Slice 4 — the #10 archetype recommendation. A derived default (market cap + purity) that DIFFERS from the
// current archetype shows pending in the DD rail; applying it persists ONE member as operator_edited (the
// operator decides). The rule ABSTAINS (no chip) when it has no hint, and stays quiet when it already agrees.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const basket = [
    { ticker: "OKLO", role: "r", archetype: "high_beta", security_id: "s-oklo", segment: null, authored_by: "operator_set", thesis_fit: null },
    { ticker: "LEU", role: "r", archetype: "shovel", security_id: "s-leu", segment: null, authored_by: "operator_set", thesis_fit: null },
    { ticker: "CCJ", role: "r", archetype: "leader", security_id: "s-ccj", segment: null, authored_by: "operator_set", thesis_fit: null },
  ];
  const members = [
    // disagreement: high_beta now, the figures suggest leader -> a pending chip + a ✦ dot
    { security_id: "s-oklo", ticker: "OKLO", archetype: "high_beta", archetype_hint: "leader", segment: null, purity: fig(4, 100), runway: fig(4, null), catalysts: fig(1, 1), dilution: fig(null, null), market_cap: fig(null, 1.2e10), fit: "pure-play" },
    // abstain: no hint (relational / no facts) -> no chip, no dot
    { security_id: "s-leu", ticker: "LEU", archetype: "shovel", archetype_hint: null, segment: null, purity: fig(3, 77), runway: fig(4, 160), catalysts: fig(2, 1), dilution: fig(null, null), market_cap: fig(null, 3e9), fit: "core exposure" },
    // agreement: hint == archetype -> quiet (no chip, no dot)
    { security_id: "s-ccj", ticker: "CCJ", archetype: "leader", archetype_hint: "leader", segment: null, purity: fig(4, 100), runway: fig(4, null), catalysts: fig(1, 1), dilution: fig(null, null), market_cap: fig(null, 5e10), fit: "pure-play" },
  ];
  const thesis = {
    id: "t-nuke",
    name: "Nuclear",
    narrative: "n",
    ticker: null,
    segments: [],
    basket,
    evidence: [],
    catalysts: [],
    kill_criteria: [],
    position: null,
  };
  const scored = { thesis_id: "t-nuke", asof: "2026-06-08", segments: [], members };
  return { thesis, scored, basket };
});

const h = vi.hoisted(() => ({ mutate: vi.fn() }));

vi.mock("../../api/hooks", () => ({
  useTheses: () => ({ data: [{ id: "t-nuke", name: "Nuclear", ticker: null, basket_size: 3, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: h.mutate,
    mutateAsync: vi.fn(),
    reset: vi.fn(),
    isPending: false,
    isError: false,
    isSuccess: false,
    error: null,
  }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useDraftChain: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () =>
  render(<Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />);

describe("Workbench — the #10 archetype recommendation (Slice 4)", () => {
  beforeEach(() => {
    h.mutate.mockReset();
  });

  it("shows the derived recommendation pending and applies it as operator_edited (one member, via promote)", async () => {
    const user = userEvent.setup();
    renderWb();
    // the DD rail (first member, OKLO, selected by default) shows the derived suggestion — pending, not applied
    expect(screen.getByTitle(/a recommendation, not a verdict/i)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /apply leader to OKLO/i }));

    expect(h.mutate).toHaveBeenCalledTimes(1);
    const payload = h.mutate.mock.calls[0][0];
    expect(payload.id).toBe("t-nuke"); // the same thesis, resent (the rest of the chain untouched)
    expect(payload.basket).toHaveLength(3);
    const oklo = payload.basket.find((b: { security_id: string }) => b.security_id === "s-oklo");
    expect(oklo.archetype).toBe("leader"); // the recommendation applied
    expect(oklo.authored_by).toBe("operator_edited"); // operator authority — never auto-applied (#10)
    // the other names are resent verbatim (a single-member edit, not a chain rewrite)
    const leu = payload.basket.find((b: { security_id: string }) => b.security_id === "s-leu");
    expect(leu.archetype).toBe("shovel");
  });

  it("only the disagreeing name carries the ✦ indicator — abstain (no hint) and agreement (hint == archetype) stay quiet", () => {
    renderWb();
    // three scored names, but only OKLO (high_beta vs suggested leader) shows the ✦ dot; LEU abstains, CCJ agrees
    expect(screen.getAllByText("✦")).toHaveLength(1);
  });
});
