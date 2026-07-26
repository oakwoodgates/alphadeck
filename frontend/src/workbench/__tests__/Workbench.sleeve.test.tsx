import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// ETF Sleeve, Slice 1 — the integration gap the unit test couldn't catch: the scored view groups/filters
// members by value-chain segment, but a surfaced `fund` sleeve carries segment: null, so in a GROUPED thesis
// (an active segment tab) it was dropped from `shownMembers` and the `fund` ScoredRow never rendered. The fix
// surfaces sleeves in their OWN section so a sleeve always renders with its price. This test renders the full
// Workbench with a segmented equity + a segment:null fund and asserts BOTH: the equity groups by segment AND
// the sleeve renders as a priced `.nmrow.fund`.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null, provenance: unknown[] = []) => ({
    pips,
    value,
    provenance,
  });
  const priceProv = [
    { source: "price", ref: "price:2026-06-10", url: null, detail: { close: 41.23 } },
  ];
  const segs = [{ label: "reactors", descriptor: "the SMR designers" }];
  const basket = [
    { ticker: "OKLO", role: "r", archetype: "leader", security_id: "s-oklo", segment: "reactors", authored_by: "operator_set", thesis_fit: null },
    // the surfaced sleeve: archetype 'fund', segment null (SurfaceEtf sets both)
    { ticker: "URA", role: "ETF sleeve", archetype: "fund", security_id: "s-ura", segment: null, authored_by: "operator_set", thesis_fit: null },
  ];
  const members = [
    { security_id: "s-oklo", ticker: "OKLO", name: "Oklo Inc.", archetype: "leader", archetype_hint: null, segment: "reactors", purity: fig(4, 100), runway: fig(4, null), catalysts: fig(1, 1), dilution: fig(null, null), market_cap: fig(null, 1.2e10), fit: "pure-play" },
    // a fund has no shares fact -> market_cap value null, price provenance carries the sleeve price
    { security_id: "s-ura", ticker: "URA", name: "Global X Uranium ETF", archetype: "fund", archetype_hint: null, segment: null, purity: fig(null, null), runway: fig(null, null), catalysts: fig(null, null), dilution: fig(null, null), market_cap: fig(null, null, priceProv), fit: "" },
  ];
  const thesis = { id: "t-nuke", name: "Nuclear", narrative: "n", ticker: null, segments: segs, basket, evidence: [], catalysts: [], kill_criteria: [], position: null };
  const scored = { thesis_id: "t-nuke", asof: "2026-06-08", segments: segs, members };
  return { thesis, scored };
});

vi.mock("../../api/hooks", () => ({
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-nuke", name: "Nuclear", ticker: null, basket_size: 2, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), reset: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useResolveEtf: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useDraftChain: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () =>
  render(<Workbench asof="2026-06-08" onAsofChange={() => {}} onBack={() => {}} />);

describe("Workbench — the `fund` sleeve renders in the scored view (ETF Sleeve, Slice 1)", () => {
  it("renders a segment:null fund as a priced .nmrow.fund while grouping the equity by segment", () => {
    const { container } = renderWb();

    // the sleeve renders as EXACTLY ONE fund ScoredRow (not dropped by the segment filter, not double-rendered)
    const funds = container.querySelectorAll(".nmrow.fund");
    expect(funds).toHaveLength(1);
    const fundRow = funds[0] as HTMLElement;
    expect(within(fundRow).getByText("URA")).toBeInTheDocument(); // it's the surfaced sleeve
    expect(within(fundRow).getByText("ETF sleeve")).toBeInTheDocument(); // the sleeve label chip
    expect(within(fundRow).getByText("$41.23")).toBeInTheDocument(); // ...WITH its price

    // the value-chain equity still groups under its segment — and is NOT a fund row
    const oklo = screen.getByRole("button", { name: /OKLO/ });
    expect(oklo.closest(".nmrow.fund")).toBeNull();

    // the sleeve carries no equity meters (it's an expression) — no purity meter inside the fund row
    expect(within(fundRow).queryByText("purity")).not.toBeInTheDocument();
  });
});
