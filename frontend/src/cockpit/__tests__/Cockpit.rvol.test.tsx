import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The two RVOL columns on the Cockpit basket table, both off the `rvol` display member (which emits
// TWO windows off one fetch): RVOL|8 (the as-of bar's volume vs the prior 8-bar average — call-
// matched) and RVOL|20 (the same idea over 20 bars — the trader "unusually active vs its month?"
// read, call-decoupled), the SAME display-signals query the SMA / return cells read, bridged by
// security_id. The load-bearing checks: both headers render (RVOL|8 renamed + RVOL|20 added), each
// column accents from its OWN loud threshold on the wire (a volume-backed move gets the WARM 'hot'
// accent, an ordinary one stays muted) INDEPENDENTLY, a volumeless as-of bar is an HONEST em-dash in
// both, and the columns survive the business-type lens (they live on the per-name row).
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
  // both loud thresholds ride the wire (basis.params.loud_mult / loud_mult_20) so the FE hardcodes
  // nothing; the member carries the 8-bar (rvol) and the 20-bar (rvol20) metrics off one fetch
  const rvolSig = (
    value: number | null,
    value20: number | null,
    note: string | null = null,
    note20: string | null = null,
  ) => ({
    kind: "rvol", label: "Relative volume",
    metrics: [
      { key: "rvol", label: "RVOL", value, unit: "ratio", tone: null, note },
      { key: "rvol20", label: "RVOL20", value: value20, unit: "ratio", tone: null, note: note20 },
    ],
    basis: {
      source: "fact_price_eod",
      params: { baseline_bars: 8, loud_mult: 1.5, baseline_bars_20: 20, loud_mult_20: 1.5, lookback_days: 55 },
      bars_used: value == null ? null : 21, window_start: null, window_end: null, note: null,
    },
  });
  const member = (
    sid: string, ticker: string,
    value: number | null, value20: number | null,
    note: string | null = null, note20: string | null = null,
  ) => ({
    security_id: sid, ticker, signals: [rvolSig(value, value20, note, note20)],
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
        // the two windows accent INDEPENDENTLY off their own thresholds: OKLO is 8-bar hot (2.4x) but
        // 20-bar quiet (1.1x); FISN is the OPPOSITE (8-bar quiet 0.9x, 20-bar hot 1.8x) — proving each
        // cell reads its OWN metric + threshold, not a shared one
        member("s-oklo", "OKLO", 2.4, 1.1),
        member("s-fisn", "FISN", 0.9, 1.8),
        // a halted as-of bar blanks BOTH windows with the honest why
        member("s-mnmd", "MNMD", null, null, "n/a: no volume on the as-of bar", "n/a: no volume on the as-of bar"),
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

describe("Cockpit — RVOL columns (8-bar + 20-bar)", () => {
  it("renders both headers — RVOL|8 (renamed) and RVOL|20 (added), not a bare RVOL", () => {
    renderCockpit();
    expect(screen.getByRole("columnheader", { name: "RVOL|8" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "RVOL|20" })).toBeInTheDocument();
    // the old single "RVOL" header is gone (renamed, not left behind)
    expect(screen.queryByRole("columnheader", { name: "RVOL" })).not.toBeInTheDocument();
  });

  it("accents each window from its OWN threshold (8-bar and 20-bar are independent)", () => {
    renderCockpit();
    // OKLO: the 8-bar 2.4x is volume-backed -> the warm 'hot' accent; its 20-bar 1.1x is quiet -> NOT
    // hot. Same row, opposite accents -> each cell reads its OWN metric + threshold (#7).
    const okloCells = (screen.getByText("OKLO").closest("tr") as HTMLElement).querySelectorAll(
      "td.rvolc .rvol",
    );
    expect(okloCells).toHaveLength(2);
    expect(okloCells[0].textContent).toBe("2.40×");
    expect(okloCells[0].className).toContain("hot");
    expect(okloCells[1].textContent).toBe("1.10×");
    expect(okloCells[1].className).not.toContain("hot");
    // FISN: the OPPOSITE — 8-bar 0.9x quiet, 20-bar 1.8x unusually active -> hot
    const fisnCells = (screen.getByText("FISN").closest("tr") as HTMLElement).querySelectorAll(
      "td.rvolc .rvol",
    );
    expect(fisnCells[0].textContent).toBe("0.90×");
    expect(fisnCells[0].className).not.toContain("hot");
    expect(fisnCells[1].textContent).toBe("1.80×");
    expect(fisnCells[1].className).toContain("hot");
  });

  it("renders a volumeless as-of bar as an honest em-dash in BOTH windows, never a number", () => {
    renderCockpit();
    const cells = (screen.getByText("MNMD").closest("tr") as HTMLElement).querySelectorAll(
      "td.rvolc",
    );
    expect(cells).toHaveLength(2);
    cells.forEach((cell) => {
      const span = cell.querySelector("span") as HTMLElement;
      expect(span.textContent).toBe("—");
      expect(span.className).toContain("muted");
      expect(span.title).toBe("n/a: no volume on the as-of bar");
    });
  });

  it("keeps BOTH RVOL columns in BOTH lenses (call-state and business-type)", () => {
    renderCockpit();
    expect(screen.getByText("2.40×")).toBeInTheDocument(); // 8-bar, default (call-state) lens
    expect(screen.getByText("1.80×")).toBeInTheDocument(); // 20-bar, default lens
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    // the lens re-groups the rows by super-sector, but both per-name RVOL cells still render
    expect(screen.getByText("2.40×")).toBeInTheDocument();
    expect(screen.getByText("1.80×")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "RVOL|8" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "RVOL|20" })).toBeInTheDocument();
  });
});
