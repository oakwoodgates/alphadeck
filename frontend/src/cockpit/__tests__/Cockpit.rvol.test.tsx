import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The RVOL column on the Cockpit basket table: the `rvol` display member (the as-of bar's volume vs
// the prior 8-bar average), the SAME display-signals query the SMA / return cells read, bridged by
// security_id. The load-bearing checks: the header renders, a volume-backed move (>= the wire's
// loud_mult) gets the WARM 'hot' accent while an ordinary one stays muted, a volumeless as-of bar is
// an HONEST em-dash with the why on hover, and the column survives the business-type lens (it lives
// on the per-name row). Mirrors the trailing-returns test's fixture shape.
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
  // the loud threshold rides the wire (basis.params.loud_mult) so the FE hardcodes nothing
  const rvolSig = (value: number | null, note: string | null = null) => ({
    kind: "rvol", label: "Relative volume",
    metrics: [{ key: "rvol", label: "RVOL", value, unit: "ratio", tone: null, note }],
    basis: {
      source: "fact_price_eod",
      params: { baseline_bars: 8, loud_mult: 1.5, lookback_days: 40 },
      bars_used: value == null ? null : 9, window_start: null, window_end: null, note: null,
    },
  });
  const member = (sid: string, ticker: string, value: number | null, note: string | null = null) => ({
    security_id: sid, ticker, signals: [rvolSig(value, note)],
  });
  return {
    thesis: {
      id: "t-nuc", name: "Nuclear Buildout", narrative: "n", ticker: null, segments: [],
      basket: [
        { ticker: "OKLO", role: "core", security_id: "s-oklo", detail: null, authored_by: "operator_set" },
        { ticker: "FISN", role: "core", security_id: "s-fisn", detail: null, authored_by: "operator_set" },
        { ticker: "MNMD", role: "core", security_id: "s-mnmd", detail: null, authored_by: "operator_set" },
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
        scoredRow("s-mnmd", "MNMD", "Halted Co", {}),
      ],
    },
    display: {
      members: [
        member("s-oklo", "OKLO", 2.4), // volume-backed -> hot (>= 1.5)
        member("s-fisn", "FISN", 0.9), // ordinary -> muted (< 1.5)
        member("s-mnmd", "MNMD", null, "n/a: no volume on the as-of bar"), // halted -> honest gap
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

describe("Cockpit — RVOL column", () => {
  it("renders the RVOL header", () => {
    renderCockpit();
    expect(screen.getByRole("columnheader", { name: "RVOL" })).toBeInTheDocument();
  });

  it("accents a volume-backed move (>= loud_mult) warm, leaves an ordinary one muted", () => {
    renderCockpit();
    // OKLO 2.4x is volume-backed -> the warm 'hot' accent (the exception, #7)
    const hot = screen.getByText("2.40×");
    expect(hot.className).toContain("rvol");
    expect(hot.className).toContain("hot");
    // FISN 0.9x is ordinary -> rendered, but NOT hot (loudness marks the exception, not every row)
    const quiet = screen.getByText("0.90×");
    expect(quiet.className).toContain("rvol");
    expect(quiet.className).not.toContain("hot");
  });

  it("renders a volumeless as-of bar as an honest em-dash with the why on hover, never a number", () => {
    renderCockpit();
    const mnmdRow = screen.getByText("MNMD").closest("tr") as HTMLElement;
    const cell = mnmdRow.querySelector("td.rvolc span") as HTMLElement;
    expect(cell.textContent).toBe("—");
    expect(cell.className).toContain("muted");
    expect(cell.title).toBe("n/a: no volume on the as-of bar");
  });

  it("keeps the RVOL column in BOTH lenses (call-state and business-type)", () => {
    renderCockpit();
    expect(screen.getByText("2.40×")).toBeInTheDocument(); // default (call-state) lens
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    // the lens re-groups the rows by super-sector, but the per-name RVOL cell still renders
    expect(screen.getByText("2.40×")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "RVOL" })).toBeInTheDocument();
  });
});
