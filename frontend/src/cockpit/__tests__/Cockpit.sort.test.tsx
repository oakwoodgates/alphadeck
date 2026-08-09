import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// Sortable basket columns (within-group). The load-bearing checks: clicking a numeric header ranks
// the group's rows desc (nulls LAST), a second click flips to asc (nulls STILL last), a third click
// turns sort off and restores the default order; the active <th> carries aria-sort + an arrow while
// the header's accessible NAME stays byte-identical (the columnheader-name assertions must survive);
// the sort PERSISTS across a lens switch (re-ranks within the new lens's groups); and it drops
// nothing (row count unchanged). Fixture shape mirrors Cockpit.trailingReturns/typelens.
const fx = vi.hoisted(() => {
  const fig = (value: number | null) => ({ pips: null, value, provenance: [] });
  const scoredRow = (sid: string, ticker: string, name: string, over: Record<string, unknown>) => ({
    security_id: sid, ticker, name, sector: "x", exchange: null, category: null,
    business_type: null, business_supersector: null, business_type_override: null,
    royalty: false, instrument_kind: "equity",
    purity: fig(null), runway: fig(null), catalysts: fig(null),
    dilution: fig(null), market_cap: fig(null), fit: "", unconfirmed_estimates: 0,
    ...over,
  });
  const metric = (key: string, value: number | null) =>
    ({ key, label: key, value, unit: "pct", tone: value == null ? null : value < 0 ? "neg" : "pos", note: null });
  const trailSig = (ret30: number | null) => ({
    kind: "trailing_returns", label: "Trailing returns", metrics: [metric("ret_30d", ret30)],
    basis: { source: "fact_price_eod", params: {}, bars_used: null, window_start: null, window_end: null, note: null },
  });
  const member = (ticker: string, sid: string) =>
    ({ ticker, role: "core", security_id: sid, detail: null, authored_by: "operator_set" });
  return {
    thesis: {
      id: "t-nuc", name: "Nuclear Buildout", narrative: "n", ticker: null, segments: [],
      // authored order OKLO, NEE, CEG, FISN — deliberately NOT the sorted order of any column, so the
      // default / desc / asc row orders are all distinct (off ≠ asc ≠ desc)
      basket: [member("OKLO", "s-oklo"), member("NEE", "s-nee"), member("CEG", "s-ceg"), member("FISN", "s-fisn")],
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    // card undefined -> every row reads Quiet (one group); the within-group sort still ranks it
    scored: {
      members: [
        scoredRow("s-oklo", "OKLO", "Oklo Inc.", { business_type: "utilities", business_supersector: "energy_utilities" }),
        scoredRow("s-nee", "NEE", "NextEra", { business_type: "utilities", business_supersector: "energy_utilities" }),
        scoredRow("s-ceg", "CEG", "Constellation", { business_type: "utilities", business_supersector: "energy_utilities" }),
        scoredRow("s-fisn", "FISN", "Thin Co", {}), // no super -> Unclassified in the type lens; no 30d -> "—"
      ],
    },
    display: {
      members: [
        { security_id: "s-oklo", ticker: "OKLO", signals: [trailSig(5)] },
        { security_id: "s-nee", ticker: "NEE", signals: [trailSig(12)] },
        { security_id: "s-ceg", ticker: "CEG", signals: [trailSig(-3)] },
        { security_id: "s-fisn", ticker: "FISN", signals: [trailSig(null)] }, // 30d absent -> nulls-last
      ],
    },
  };
});

vi.mock("../../api/hooks", () => ({
  useThesis: () => ({ data: fx.thesis, isLoading: false, error: null }),
  useCall: () => ({ data: undefined, isLoading: false, error: null }),
  useWorkbenchScored: () => ({ data: fx.scored, isLoading: false, error: null }),
  useDisplaySignals: () => ({ data: fx.display, isLoading: false, error: null }),
  usePutCatalysts: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
  usePutKillCriteria: () => ({ mutate: () => {}, isPending: false, isError: false, error: null }),
}));

import { Cockpit } from "../Cockpit";

function renderCockpit() {
  return render(
    <Cockpit
      thesisId="t-nuc"
      asof="2026-08-09"
      onAsofChange={() => {}}
      onBack={() => {}}
      selectedName={null}
      onSelectName={() => {}}
    />,
  );
}

const rowTickers = (c: HTMLElement) =>
  [...c.querySelectorAll("tbody tr.bkt")].map((tr) => tr.querySelector("td.tk")?.textContent?.trim());

function clickSort(label: string) {
  const th = screen.getByRole("columnheader", { name: label });
  fireEvent.click(th.querySelector("button.th-sort") as HTMLElement);
}

describe("Cockpit — sortable basket columns (within-group)", () => {
  it("defaults to the authored order (no sort active)", () => {
    const { container } = renderCockpit();
    expect(rowTickers(container)).toEqual(["OKLO", "NEE", "CEG", "FISN"]);
    // no header is marked active by default
    expect(screen.getByRole("columnheader", { name: "30d" })).toHaveAttribute("aria-sort", "none");
  });

  it("ranks the group desc on the first click, with the '—' row LAST", () => {
    const { container } = renderCockpit();
    clickSort("30d");
    // 12, 5, -3, then FISN (no 30d) last
    expect(rowTickers(container)).toEqual(["NEE", "OKLO", "CEG", "FISN"]);
    const th = screen.getByRole("columnheader", { name: "30d" });
    expect(th).toHaveAttribute("aria-sort", "descending");
    // the arrow renders but is aria-hidden (so the header NAME is still exactly "30d")
    expect(th.querySelector(".th-arrow")?.getAttribute("aria-hidden")).toBe("true");
  });

  it("flips to asc on the second click; the '—' row stays LAST", () => {
    const { container } = renderCockpit();
    clickSort("30d");
    clickSort("30d");
    // -3, 5, 12, then FISN last (nulls-last does NOT flip with direction)
    expect(rowTickers(container)).toEqual(["CEG", "OKLO", "NEE", "FISN"]);
    expect(screen.getByRole("columnheader", { name: "30d" })).toHaveAttribute("aria-sort", "ascending");
  });

  it("turns sort off on the third click and restores the default order", () => {
    const { container } = renderCockpit();
    clickSort("30d");
    clickSort("30d");
    clickSort("30d");
    expect(rowTickers(container)).toEqual(["OKLO", "NEE", "CEG", "FISN"]); // back to authored order
    const th = screen.getByRole("columnheader", { name: "30d" });
    expect(th).toHaveAttribute("aria-sort", "none");
    expect(th.querySelector(".th-arrow")).toBeNull(); // no arrow when off
  });

  it("keeps every header's accessible name byte-identical while a sort is active", () => {
    renderCockpit();
    clickSort("30d"); // activate a sort — the arrow must not pollute any header name
    for (const name of ["Ticker", "Type", "1d", "30d", "1Y", "RVOL|8", "RVOL|20", "Ins 30d", "Mkt cap", "Exit-by"]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
    // exact-name matching is preserved: "RVOL" must NOT match "RVOL|8"
    expect(screen.queryByRole("columnheader", { name: "RVOL" })).not.toBeInTheDocument();
  });

  it("persists the sort across a lens switch, re-ranking within the new lens's groups", () => {
    const { container } = renderCockpit();
    clickSort("30d"); // desc
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    // energy_utilities group (OKLO/NEE/CEG) ranked desc, then the Unclassified FISN — the sort
    // survived the lens change and applies WITHIN each super-sector group
    expect(rowTickers(container)).toEqual(["NEE", "OKLO", "CEG", "FISN"]);
    expect(screen.getByRole("columnheader", { name: "30d" })).toHaveAttribute("aria-sort", "descending");
  });

  it("re-orders without dropping a row (a re-order, never a filter)", () => {
    const { container } = renderCockpit();
    const before = container.querySelectorAll("tbody tr.bkt").length;
    clickSort("30d");
    clickSort("30d");
    const after = container.querySelectorAll("tbody tr.bkt").length;
    expect(after).toBe(before);
    expect(after).toBe(4);
    // and every name is still present after the sort
    for (const t of ["OKLO", "NEE", "CEG", "FISN"]) {
      expect(within(container.querySelector("tbody") as HTMLElement).getByText(t)).toBeInTheDocument();
    }
  });
});
