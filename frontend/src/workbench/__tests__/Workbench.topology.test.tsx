import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// #1–3 (chain-editing Phase 2) — value-chain TOPOLOGY on the SCORE view, end to end through the Workbench:
// the ✎ edit links reveal exposes per-link rename / reorder / remove + the `+ add link` bootstrap, each an
// immediate-promote built FROM THE SPINE (thesis.basket/segments). Rename cascades onto members; reorder
// swaps; add appends; remove is multi-safe and routes a last placement to the Discovered floor. Plus the
// two-screen relabel (the button reads "Edit the basket").

const mk = vi.hoisted(() => {
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
  const bm = (ticker: string, sid: string, segment: string | null) => ({
    ticker, role: "core", security_id: sid, detail: null, segment,
    thesis_fit: null, conviction: null, surfaced_terms: [] as string[],
    authored_by: "operator_set" as const, signed_off: false,
  });
  const wrap = (
    segments: { label: string; descriptor: string | null }[],
    basket: ReturnType<typeof bm>[],
    members: ReturnType<typeof scoredRow>[],
  ) => ({
    thesis: {
      id: "t-nuke", name: "Nuclear", narrative: "n", ticker: null, segments, basket,
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    scored: { thesis_id: "t-nuke", asof: "2026-06-08", segments, members },
  });
  // grouped: OKLO in reactors AND fuel (multi-membership); LEU only in fuel
  const grouped = () =>
    wrap(
      [{ label: "reactors", descriptor: null }, { label: "fuel", descriptor: null }],
      [bm("OKLO", "s-oklo", "reactors"), bm("OKLO", "s-oklo", "fuel"), bm("LEU", "s-leu", "fuel")],
      [scoredRow("s-oklo", "OKLO", "reactors"), scoredRow("s-leu", "LEU", "fuel")],
    );
  // flat: no links yet (the pre-decompose basket) — the + add link bootstrap builds the first
  const flat = () =>
    wrap([], [bm("OKLO", "s-oklo", null), bm("LEU", "s-leu", null)], [
      scoredRow("s-oklo", "OKLO", null),
      scoredRow("s-leu", "LEU", null),
    ]);
  return { grouped, flat };
});

const state = vi.hoisted(() => ({ thesis: undefined as unknown, scored: undefined as unknown }));
const h = vi.hoisted(() => ({ promote: vi.fn() }));

vi.mock("../../api/hooks", () => ({
  useSetBusinessType: () => ({ mutate: vi.fn(), isPending: false }),
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-nuke", name: "Nuclear", ticker: null, basket_size: 3, narrative: "n" }] }),
  useThesis: () => ({ data: state.thesis }),
  useWorkbenchScored: () => ({ data: state.scored, isLoading: false, error: null }),
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
const enterEditLinks = async (user: ReturnType<typeof userEvent.setup>) =>
  user.click(screen.getByRole("button", { name: /edit links/i }));
const lastPayload = () => h.promote.mock.calls.at(-1)![0];
const labels = (segs: { label: string }[]) => segs.map((s) => s.label);

beforeEach(() => {
  const g = mk.grouped();
  state.thesis = g.thesis;
  state.scored = g.scored;
  h.promote.mockClear();
});

describe("Workbench — value-chain topology on SCORE (#1–3, chain-editing Phase 2)", () => {
  it("the two-screen relabel: the button reads 'Edit the basket', not 'Edit the chain'", () => {
    renderWb();
    expect(screen.getByRole("button", { name: /edit the basket/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit the chain/i })).toBeNull();
  });

  it("#1 rename CASCADES the new label onto every placed member", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    const input = screen.getByLabelText("rename reactors");
    await user.clear(input);
    await user.type(input, "reactor builders{Enter}");

    const p = lastPayload();
    expect(labels(p.segments)).toEqual(["reactor builders", "fuel"]);
    // OKLO's reactors row cascaded; no member is left pointing at the old label (no orphan)
    expect(p.basket.some((b: { security_id: string; segment: string }) => b.security_id === "s-oklo" && b.segment === "reactor builders")).toBe(true);
    expect(p.basket.some((b: { segment: string }) => b.segment === "reactors")).toBe(false);
  });

  it("#2 reorder swaps and persists the link order", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    await user.click(screen.getByLabelText("move fuel left"));
    expect(labels(lastPayload().segments)).toEqual(["fuel", "reactors"]);
  });

  it("#3 add appends a new link", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    await user.type(screen.getByLabelText("new link name"), "supply");
    await user.click(screen.getByRole("button", { name: "+ add link" }));
    expect(labels(lastPayload().segments)).toEqual(["reactors", "fuel", "supply"]);
  });

  it("#3 remove is multi-safe: a name kept elsewhere loses only its redundant row", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    await user.click(screen.getByLabelText("remove reactors"));

    const p = lastPayload();
    expect(labels(p.segments)).toEqual(["fuel"]);
    const oklo = p.basket.filter((b: { security_id: string }) => b.security_id === "s-oklo");
    expect(oklo).toHaveLength(1);
    expect(oklo[0].segment).toBe("fuel"); // kept placement, not floored
    expect(p.basket.some((b: { segment: string }) => b.segment === "Discovered")).toBe(false);
  });

  it("#3 remove routes a LAST placement to the Discovered floor (never null / gone)", async () => {
    const user = userEvent.setup();
    renderWb();
    await enterEditLinks(user);
    await user.click(screen.getByLabelText("remove fuel"));

    const p = lastPayload();
    const leu = p.basket.find((b: { security_id: string }) => b.security_id === "s-leu");
    expect(leu.segment).toBe("Discovered"); // LEU was only in fuel → floored, not dropped (#9)
    expect(p.segments.some((s: { label: string }) => s.label === "Discovered")).toBe(true);
    expect(p.segments.some((s: { label: string }) => s.label === "fuel")).toBe(false);
  });

  it("bootstrap: + add link builds the FIRST link from the flat (pre-decompose) state", async () => {
    const user = userEvent.setup();
    const f = mk.flat();
    state.thesis = f.thesis;
    state.scored = f.scored;
    renderWb();
    // flat: no tab strip; the toggle is still there so the first link is buildable
    await enterEditLinks(user);
    expect(screen.getByText(/name your first one/i)).toBeInTheDocument();
    await user.type(screen.getByLabelText("new link name"), "reactors");
    await user.click(screen.getByRole("button", { name: "+ add link" }));

    const p = lastPayload();
    expect(labels(p.segments)).toEqual(["reactors"]);
    // the flat basket rides through untouched (null segments stay valid — the validator only rejects orphans)
    expect(p.basket).toHaveLength(2);
    expect(p.basket.every((b: { segment: string | null }) => b.segment === null)).toBe(true);
  });
});
