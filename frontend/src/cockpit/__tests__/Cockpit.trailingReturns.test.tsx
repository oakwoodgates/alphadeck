import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The trailing-return columns (1d/7d/30d/90d) on the Cockpit basket table: four EOD price-return
// cells per name, fed by the `trailing_returns` display member (the SAME display-signals query the
// SMA cell reads, bridged by security_id). The load-bearing checks: the four headers render, a
// name's returns show signed/colored, a thin-history 90d is an HONEST em-dash (never a fake number),
// and the columns survive the business-type lens (they live on the per-name row, so BOTH lenses show
// them). Mirrors the type-lens test's scored-row fixture shape.
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
  const metric = (key: string, value: number | null, tone: string | null, note: string | null = null) =>
    ({ key, label: key.replace("ret_", ""), value, unit: "pct", tone, note });
  const trailSig = (metrics: unknown[]) => ({
    kind: "trailing_returns", label: "Trailing returns", metrics,
    basis: { source: "fact_price_eod", params: {}, bars_used: null, window_start: null, window_end: null, note: null },
  });
  return {
    thesis: {
      id: "t-nuc", name: "Nuclear Buildout", narrative: "n", ticker: null, segments: [],
      basket: [
        { ticker: "OKLO", role: "core", security_id: "s-oklo", detail: null, authored_by: "operator_set" },
        { ticker: "FISN", role: "core", security_id: "s-fisn", detail: null, authored_by: "operator_set" },
      ],
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    // card undefined -> every row reads Quiet (honest while the call computes); the lens still works
    scored: {
      members: [
        scoredRow("s-oklo", "OKLO", "Oklo Inc.", {
          business_type: "nuclear_smr", business_supersector: "energy_utilities", market_cap: fig(5e9),
        }),
        scoredRow("s-fisn", "FISN", "Thin Co", {}), // no super-sector -> Unclassified in the type lens
      ],
    },
    display: {
      members: [
        {
          security_id: "s-oklo", ticker: "OKLO",
          signals: [trailSig([
            metric("ret_1d", -1.86, "neg"), metric("ret_7d", 6.59, "pos"),
            metric("ret_30d", -21.96, "neg"), metric("ret_90d", -16.01, "neg"),
          ])],
        },
        {
          security_id: "s-fisn", ticker: "FISN",
          signals: [trailSig([
            metric("ret_1d", 5.98, "pos"), metric("ret_7d", -6.77, "neg"),
            metric("ret_30d", -5.53, "neg"), metric("ret_90d", null, null, "n/a: 34/91 bars"),
          ])],
        },
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

describe("Cockpit — trailing-return columns (1d/7d/30d/90d)", () => {
  it("renders the four window headers", () => {
    renderCockpit();
    // the headers are honest EOD trading-day windows (1d = last close vs the prior close, never 24h)
    for (const w of ["1d", "7d", "30d", "90d"]) {
      expect(screen.getByRole("columnheader", { name: w })).toBeInTheDocument();
    }
  });

  it("renders each name's returns signed and color-coded (green up / red down)", () => {
    renderCockpit();
    // OKLO: 7d up is green, 30d down is red (values render at 1dp via fmtMetricValue)
    const up = screen.getByText("+6.6%");
    expect(up.className).toContain("pos");
    const down = screen.getByText("-22.0%");
    expect(down.className).toContain("neg");
    // FISN's 1d up also renders (both names get their own row of cells)
    expect(screen.getByText("+6.0%").className).toContain("pos");
  });

  it("renders a thin-history 90d as an honest em-dash with the why on hover, never a number", () => {
    renderCockpit();
    const fisnRow = screen.getByText("FISN").closest("tr") as HTMLElement;
    const retCells = fisnRow.querySelectorAll("td.retc");
    expect(retCells).toHaveLength(4);
    const gap = retCells[3].querySelector("span") as HTMLElement; // the 90d cell
    expect(gap.textContent).toBe("—");
    expect(gap.className).toContain("muted");
    expect(gap.title).toBe("n/a: 34/91 bars");
  });

  it("keeps the return columns in BOTH lenses (call-state and business-type)", () => {
    renderCockpit();
    expect(screen.getByText("+6.6%")).toBeInTheDocument(); // default (call-state) lens
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    // the lens re-groups the rows by super-sector, but the per-name return cells still render
    expect(screen.getByText("+6.6%")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "30d" })).toBeInTheDocument();
  });
});
