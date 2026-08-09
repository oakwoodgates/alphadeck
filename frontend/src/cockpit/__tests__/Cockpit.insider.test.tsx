import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The two insider-buys columns on the Cockpit basket table — Ins 30d and Ins 90d — both off the
// `insider_flow_90d` display member (which now emits a 30d sub-window alongside its 90d metrics off
// ONE fetch), the SAME display-signals query the SMA / return / RVOL cells read, bridged by
// security_id. Each cell renders `{open-market buys}/{distinct buyers}` for its window. The
// load-bearing checks: both headers render; "8/3" renders; a CLUSTER (>=2 distinct buyers) accents
// while a lone buyer shows un-accented (breadth is the conviction tell, #7) INDEPENDENTLY per window;
// zero buys (and an absent member) read an honest muted "—"; the columns survive both cockpit lenses
// (they live on the per-name row); and the group header spans all 16 columns.
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
  // the member carries the 30d + 90d counts off ONE fetch; the cell reads the counts only (the USD
  // + sell metrics ride the same payload for the panel, omitted here for a readable fixture)
  const insiderSig = (buys30: number, buyers30: number, buys90: number, buyers90: number) => ({
    kind: "insider_flow_90d", label: "Insider flow (90d, open-market)",
    metrics: [
      { key: "buy_count", label: "buys", value: buys90, unit: "count", tone: null, note: null },
      { key: "sell_count", label: "sells", value: 0, unit: "count", tone: null, note: null },
      { key: "distinct_buyers", label: "buyers", value: buyers90, unit: "count", tone: null, note: null },
      { key: "buy_count_30d", label: "buys 30d", value: buys30, unit: "count", tone: null, note: null },
      { key: "distinct_buyers_30d", label: "buyers 30d", value: buyers30, unit: "count", tone: null, note: null },
    ],
    events: [],
    basis: {
      source: "fact_insider_txn",
      params: { window_days: 90, window_days_short: 30, offmarket_below_low_frac: 0.1, max_plausible_txn_usd: 2e9 },
      bars_used: null, window_start: null, window_end: null, note: null,
    },
  });
  const member = (sid: string, ticker: string, sig: unknown | null) => ({
    security_id: sid, ticker, signals: sig ? [sig] : [],
  });
  return {
    thesis: {
      id: "t-nuc", name: "Nuclear Buildout", narrative: "n", ticker: null, segments: [],
      basket: [
        { ticker: "OKLO", role: "core", security_id: "s-oklo", detail: null, authored_by: "operator_set" },
        { ticker: "FISN", role: "core", security_id: "s-fisn", detail: null, authored_by: "operator_set" },
        { ticker: "MNMD", role: "core", security_id: "s-mnmd", detail: null, authored_by: "operator_set" },
        { ticker: "NUNM", role: "core", security_id: "s-nunm", detail: null, authored_by: "operator_set" },
      ],
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    // card undefined -> every row reads Quiet (honest while the call computes); the lens still works
    scored: {
      members: [
        scoredRow("s-oklo", "OKLO", "Oklo Inc.", {
          business_type: "nuclear_smr", business_supersector: "energy_utilities",
        }),
        scoredRow("s-fisn", "FISN", "Fission Co", {}),
        scoredRow("s-mnmd", "MNMD", "Quiet Co", {}),
        scoredRow("s-nunm", "NUNM", "Unenriched Co", {}),
      ],
    },
    display: {
      members: [
        // OKLO: a cluster in BOTH windows (8/3 recent, 12/4 over the quarter) -> accented in both
        member("s-oklo", "OKLO", insiderSig(8, 3, 12, 4)),
        // FISN: a LONE buyer in 30d (2/1 — shown, but NOT accented) yet a cluster in 90d (6/3 —
        // accented) -> proves each window reads its OWN buyer count, breadth is the tell (#7)
        member("s-fisn", "FISN", insiderSig(2, 1, 6, 3)),
        // MNMD: an ingested name with zero buys in both windows -> a muted "—", never "0/0"
        member("s-mnmd", "MNMD", insiderSig(0, 0, 0, 0)),
        // NUNM: NO insider signal at all (nothing ingested) -> the metric is absent -> muted "—"
        member("s-nunm", "NUNM", null),
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

const insCells = (ticker: string) =>
  (screen.getByText(ticker).closest("tr") as HTMLElement).querySelectorAll("td.insc");

describe("Cockpit — insider-buys columns (Ins 30d + Ins 90d)", () => {
  it("renders both headers, short window before long (matching the return ladder)", () => {
    renderCockpit();
    expect(screen.getByRole("columnheader", { name: "Ins 30d" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Ins 90d" })).toBeInTheDocument();
  });

  it("renders {buys}/{buyers} as '8/3' and accents a cluster (>=2 buyers) in both windows", () => {
    renderCockpit();
    const cells = insCells("OKLO");
    expect(cells).toHaveLength(2);
    const c30 = cells[0].querySelector(".insider") as HTMLElement;
    const c90 = cells[1].querySelector(".insider") as HTMLElement;
    expect(c30.textContent).toBe("8/3");
    expect(c30.className).toContain("cluster");
    expect(c30.title).toBe("8 open-market buys by 3 insiders, last 30d");
    expect(c90.textContent).toBe("12/4");
    expect(c90.className).toContain("cluster");
    expect(c90.title).toBe("12 open-market buys by 4 insiders, last 90d");
  });

  it("shows a lone buyer un-accented but accents a cluster — per window, independently", () => {
    renderCockpit();
    const cells = insCells("FISN");
    // 30d is 2/1 — a single buyer: it SHOWS (never hidden) but carries NO cluster accent
    const c30 = cells[0].querySelector(".insider") as HTMLElement;
    expect(c30.textContent).toBe("2/1");
    expect(c30.className).not.toContain("cluster");
    expect(c30.title).toBe("2 open-market buys by 1 insider, last 30d");
    // 90d is 6/3 — a cluster in the SAME row -> accented: each window reads its OWN buyer count (#7)
    const c90 = cells[1].querySelector(".insider") as HTMLElement;
    expect(c90.textContent).toBe("6/3");
    expect(c90.className).toContain("cluster");
  });

  it("renders a muted em-dash for zero buys AND for an absent member, never a number", () => {
    renderCockpit();
    for (const ticker of ["MNMD", "NUNM"]) {
      const cells = insCells(ticker);
      expect(cells).toHaveLength(2);
      cells.forEach((cell) => {
        const span = cell.querySelector("span") as HTMLElement;
        expect(span.textContent).toBe("—");
        expect(span.className).toContain("muted");
        expect(cell.querySelector(".insider")).toBeNull(); // no value span, no accent
      });
    }
  });

  it("keeps BOTH insider columns in BOTH lenses (call-state and business-type)", () => {
    renderCockpit();
    expect(screen.getByText("8/3")).toBeInTheDocument(); // 30d, default (call-state) lens
    expect(screen.getByText("12/4")).toBeInTheDocument(); // 90d, default lens
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    // the lens re-groups the rows by super-sector, but both per-name insider cells still render
    expect(screen.getByText("8/3")).toBeInTheDocument();
    expect(screen.getByText("12/4")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Ins 30d" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Ins 90d" })).toBeInTheDocument();
  });

  it("spans the group header across all 16 columns (the two insider columns added)", () => {
    const { container } = renderCockpit();
    const grpCell = container.querySelector("tr.grp > td") as HTMLTableCellElement;
    expect(grpCell.colSpan).toBe(16);
  });
});
