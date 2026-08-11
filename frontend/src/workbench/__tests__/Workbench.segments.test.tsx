import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// #4 (chain-editing Phase 1) — the SCORE-view value-chain mover, end to end through the Workbench:
// the DD-rail multi-select builds an immediate-promote payload FROM THE SPINE (`thesis.basket`, which
// carries the per-name fields the scored read does not), and a NULL/orphan name surfaces under a
// synthesized Discovered tab instead of vanishing (#9/WB#2). Sibling handlers (the business-type re-tag)
// stay independent.
const fx = vi.hoisted(() => {
  const fig = (pips: number | null, value: number | null) => ({ pips, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string, segment: string | null) => ({
    security_id: sid, ticker, name: `${ticker} Inc`, sector: null, exchange: null, category: null,
    origin: null, foreign_filer_form: null, price_symbol: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity", segment,
    purity: fig(null, null), runway: fig(null, null), catalysts: fig(0, 0),
    dilution: fig(null, null), market_cap: fig(null, null), fit: "unrated",
    unconfirmed_estimates: 0, thin_price_history: false,
  });
  const bm = (
    ticker: string,
    sid: string,
    segment: string | null,
    over: Record<string, unknown> = {},
  ) => ({
    ticker, role: "core", security_id: sid, detail: null, segment,
    thesis_fit: null, conviction: null, surfaced_terms: [] as string[],
    authored_by: "operator_set" as const, signed_off: false, ...over,
  });
  const segments = [
    { label: "reactors", descriptor: null },
    { label: "fuel", descriptor: null },
  ];
  // OKLO carries DISTINCTIVE per-name fields — none of which the scored read (ScoredMemberOut) exposes.
  // If the promote payload preserves them, it was built from the spine, not re-derived from the scored read.
  const basket = [
    bm("OKLO", "s-oklo", "reactors", {
      conviction: 4, signed_off: true, surfaced_terms: ["smr"], authored_by: "operator_edited",
    }),
    bm("SMR", "s-smr", "fuel"),
    bm("LEU", "s-leu", null), // unsorted (null) → the synthesized Discovered tab
  ];
  const members = [
    scoredRow("s-oklo", "OKLO", "reactors"),
    scoredRow("s-smr", "SMR", "fuel"),
    scoredRow("s-leu", "LEU", null),
  ];
  const thesis = {
    id: "t-nuke", name: "Nuclear", narrative: "n", ticker: null, segments, basket,
    evidence: [], catalysts: [], kill_criteria: [], position: null,
  };
  const scored = { thesis_id: "t-nuke", asof: "2026-06-08", segments, members };
  return { thesis, scored };
});

const h = vi.hoisted(() => ({ promote: vi.fn(), retag: vi.fn() }));

vi.mock("../../api/hooks", () => ({
  useSetBusinessType: () => ({ mutate: h.retag, isPending: false }),
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-nuke", name: "Nuclear", ticker: null, basket_size: 3, narrative: "n" }] }),
  useThesis: () => ({ data: fx.thesis }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  usePromoteThesis: () => ({
    mutate: h.promote, mutateAsync: vi.fn(), reset: vi.fn(),
    isPending: false, isError: false, isSuccess: false, error: null,
  }),
  useResolveSecurities: () => ({ data: [], isFetching: false }),
  useSectionData: () => ({ run: vi.fn(), running: false, report: null, reset: vi.fn() }),
  useIngestPrices: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useAutoConfirmShares: () => ({ mutate: vi.fn(), isPending: false, isError: false, error: null }),
  useEtfHoldings: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useExtract: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useRatifyFact: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useExplainFlag: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
  useDraftChain: () => ({ data: undefined, error: null, isFetching: false, refetch: vi.fn() }),
}));

import { Workbench } from "../Workbench";

const renderWb = () => render(<Workbench asof="2026-06-08" />);
const segTabs = (root: HTMLElement) =>
  Array.from(root.querySelectorAll(".chain .seg .sn")).map((e) => e.textContent);
const tickersIn = (root: HTMLElement) =>
  Array.from(root.querySelectorAll(".nmrow .tk")).map((e) => e.textContent);

beforeEach(() => {
  h.promote.mockClear();
  h.retag.mockClear();
});

describe("Workbench — the #4 value-chain mover (chain-editing Phase 1)", () => {
  it("groups by effective segment: a null-segment name surfaces under a synthesized Discovered tab", async () => {
    const user = userEvent.setup();
    const { container } = renderWb();
    // the real links PLUS a synthesized Discovered tab for the unsorted (null) name
    expect(segTabs(container)).toEqual(["reactors", "fuel", "Discovered"]);
    // default = reactors → OKLO; the unsorted LEU is NOT dropped, it's under Discovered
    expect(tickersIn(container)).toEqual(["OKLO"]);
    await user.click(screen.getByRole("button", { name: /Discovered/ }));
    expect(tickersIn(container)).toEqual(["LEU"]);
  });

  it("moving a name (add a link) builds the promote payload from thesis.basket — carrying the spine fields", async () => {
    const user = userEvent.setup();
    renderWb(); // OKLO is the default rail selection (first name in the first link)
    // OKLO is in reactors; add fuel → a 2-link (multi-membership) name
    await user.click(screen.getByRole("checkbox", { name: "fuel" }));
    expect(h.promote).toHaveBeenCalledTimes(1);

    const payload = h.promote.mock.calls[0][0];
    const oklo = payload.basket.filter((b: { security_id: string }) => b.security_id === "s-oklo");
    expect(oklo.map((r: { segment: string }) => r.segment).sort()).toEqual(["fuel", "reactors"]);
    // EVERY row carries the spine's per-name fields (proof it came from thesis.basket, not the scored read)
    for (const r of oklo) {
      expect(r).toMatchObject({
        conviction: 4, signed_off: true, surfaced_terms: ["smr"], authored_by: "operator_edited",
      });
    }
    // the other names ride through untouched
    expect(payload.basket.find((b: { security_id: string }) => b.security_id === "s-smr")).toMatchObject({ segment: "fuel" });
    expect(payload.basket.find((b: { security_id: string }) => b.security_id === "s-leu")).toMatchObject({ segment: null });
    // sibling handler untouched — the segment move never touches the business-type re-tag
    expect(h.retag).not.toHaveBeenCalled();
  });

  it("clearing every link floors the name to a visible Discovered row (never null / gone)", async () => {
    const user = userEvent.setup();
    renderWb();
    // OKLO's only link is reactors — unchecking it clears the set → the Discovered floor
    await user.click(screen.getByRole("checkbox", { name: "reactors" }));
    expect(h.promote).toHaveBeenCalledTimes(1);

    const payload = h.promote.mock.calls[0][0];
    const oklo = payload.basket.filter((b: { security_id: string }) => b.security_id === "s-oklo");
    expect(oklo).toHaveLength(1);
    expect(oklo[0].segment).toBe("Discovered"); // the floor — a real, visible, re-selectable row
    expect(oklo[0]).toMatchObject({ conviction: 4, signed_off: true }); // per-name fields survive
    // the Discovered Segment is ensured so the promote can't 422 the consistency validator
    expect(payload.segments.some((s: { label: string }) => s.label === "Discovered")).toBe(true);
  });

  it("the business-type re-tag still routes to its own hook (sibling handler intact)", async () => {
    const user = userEvent.setup();
    renderWb();
    await user.selectOptions(screen.getByLabelText("set business type for OKLO"), "semiconductors");
    expect(h.retag).toHaveBeenCalledWith({ securityId: "s-oklo", businessType: "semiconductors" });
    expect(h.promote).not.toHaveBeenCalled(); // master-level identity — never the spine promote
  });
});
