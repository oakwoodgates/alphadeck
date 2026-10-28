import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

// The CONTEXT ROLE through the Workbench, end to end: the DD-rail select builds an immediate-promote payload
// FROM THE SPINE (`thesis.basket`) with the role on EVERY row of the company (a company in N links is N rows),
// every other name's rows and every per-name field riding through untouched; the rail's current value and the
// scored row's quiet tag both read the spine by security_id; the tag renders only when a role is set (WB#3).
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
    authored_by: "operator_set" as const, signed_off: false, context_role: null as string | null,
    ...over,
  });
  const segments = [
    { label: "reactors", descriptor: null },
    { label: "fuel", descriptor: null },
  ];
  const members = [
    scoredRow("s-oklo", "OKLO", "reactors"),
    scoredRow("s-smr", "SMR", "reactors"),
  ];
  const scored = { thesis_id: "t-nuke", asof: "2026-06-08", segments, members };
  const thesisOf = (basket: unknown[]) => ({
    id: "t-nuke", name: "Nuclear", narrative: "n", ticker: null, segments, basket,
    evidence: [], catalysts: [], kill_criteria: [], position: null,
  });
  // OKLO sits in TWO links (two rows) with distinctive per-name fields the scored read does not expose
  const oklo = (role: string | null) => [
    bm("OKLO", "s-oklo", "reactors", { conviction: 4, signed_off: true, surfaced_terms: ["smr"], authored_by: "operator_edited", context_role: role }),
    bm("OKLO", "s-oklo", "fuel", { conviction: 4, signed_off: true, surfaced_terms: ["smr"], authored_by: "operator_edited", context_role: role }),
  ];
  const state = { thesis: thesisOf([...oklo(null), bm("SMR", "s-smr", "reactors")]) as unknown };
  return { state, scored, thesisOf, oklo, bm };
});

const h = vi.hoisted(() => ({ promote: vi.fn() }));

vi.mock("../../api/hooks", () => ({
  useSetBusinessType: () => ({ mutate: vi.fn(), isPending: false }),
  useTriageSession: () => ({ data: { session: null }, isSuccess: true, isLoading: false, isError: false, error: null, refetch: vi.fn() }),
  usePutTriageSession: () => ({ mutate: vi.fn(), isPending: false, isError: false, isSuccess: false, error: null }),
  useDeleteTriageSession: () => ({ mutate: vi.fn() }),
  useTheses: () => ({ data: [{ id: "t-nuke", name: "Nuclear", ticker: null, basket_size: 2, narrative: "n" }] }),
  useThesis: () => ({ data: fx.state.thesis }),
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
type Row = { security_id: string; segment: string | null; context_role?: string | null };
const rowsFor = (payload: { basket: Row[] }, sid: string) =>
  payload.basket.filter((b) => b.security_id === sid);
const rowTags = (root: HTMLElement) =>
  Array.from(root.querySelectorAll(".nmrow .role-tag")).map((e) => e.textContent);

beforeEach(() => {
  h.promote.mockClear();
  fx.state.thesis = fx.thesisOf([...fx.oklo(null), fx.bm("SMR", "s-smr", "reactors")]);
});

describe("Workbench — the context role (display metadata, per company per card)", () => {
  it("choosing a role promotes with that role on BOTH of the company's rows and every spine field intact", async () => {
    const user = userEvent.setup();
    renderWb(); // OKLO is the default rail selection
    await user.selectOptions(screen.getByRole("combobox", { name: "Context role" }), "payer");

    expect(h.promote).toHaveBeenCalledTimes(1);
    const payload = h.promote.mock.calls[0][0];
    const oklo = rowsFor(payload, "s-oklo");
    expect(oklo.map((r) => r.segment).sort()).toEqual(["fuel", "reactors"]);
    for (const r of oklo) {
      expect(r).toMatchObject({
        context_role: "payer", conviction: 4, signed_off: true, surfaced_terms: ["smr"],
        authored_by: "operator_edited",
      });
    }
    // the other company is untouched: same row, no role invented
    expect(rowsFor(payload, "s-smr")).toHaveLength(1);
    expect(rowsFor(payload, "s-smr")[0].context_role).toBeNull();
    // the chain rides along unchanged
    expect(payload.segments.map((s: { label: string }) => s.label)).toEqual(
      expect.arrayContaining(["reactors", "fuel"]),
    );
  });

  it("the rail shows the company's role from the spine, and '— none' clears it on every row", async () => {
    fx.state.thesis = fx.thesisOf([...fx.oklo("counter_case"), fx.bm("SMR", "s-smr", "reactors")]);
    const user = userEvent.setup();
    renderWb();
    const select = screen.getByRole("combobox", { name: "Context role" });
    expect(select).toHaveValue("counter_case");

    await user.selectOptions(select, "— none");
    expect(h.promote).toHaveBeenCalledTimes(1);
    const payload = h.promote.mock.calls[0][0];
    expect(rowsFor(payload, "s-oklo").map((r) => r.context_role)).toEqual([null, null]);
    expect(rowsFor(payload, "s-oklo")).toHaveLength(2); // nothing dropped: clearing is not deleting
  });

  it("changing one role to another replaces it on every row (a company never carries two)", async () => {
    fx.state.thesis = fx.thesisOf([...fx.oklo("payer"), fx.bm("SMR", "s-smr", "reactors")]);
    const user = userEvent.setup();
    renderWb();
    await user.selectOptions(screen.getByRole("combobox", { name: "Context role" }), "financier");
    const payload = h.promote.mock.calls[0][0];
    expect(rowsFor(payload, "s-oklo").map((r) => r.context_role)).toEqual(["financier", "financier"]);
  });

  it("the scored row carries a quiet lowercase tag ONLY for a company that has a role", () => {
    fx.state.thesis = fx.thesisOf([...fx.oklo("counter_case"), fx.bm("SMR", "s-smr", "reactors")]);
    const { container } = renderWb();
    expect(rowTags(container)).toEqual(["counter-case"]); // OKLO only; SMR has none and shows no placeholder
    expect(container.querySelectorAll(".nmrow").length).toBe(2);
    const tag = container.querySelector(".nmrow .role-tag") as HTMLElement;
    expect(tag).toHaveAttribute("title", "Loses if the thesis plays out, or wins if it fails.");
  });

  it("no role anywhere → no tag on any row", () => {
    const { container } = renderWb();
    expect(rowTags(container)).toEqual([]);
  });
});
