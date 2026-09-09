import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

// The close-path sparkline column ("Path") on the Cockpit basket table: one NON-sortable cell per
// name, fed by the `price_path` display member's fixed-slot `close` series (the SAME display-signals
// query the return ladder reads, bridged by security_id). Load-bearing: the header sits right after
// 1Y and is NOT sortable (no sort button, no aria-sort — and every existing header keeps its exact
// accessible name); a full series draws one path across the cell; a left-padded (young) series draws
// a SHORTER, right-aligned path — the gap breaks the line, nothing is interpolated; a single close is
// an honest "—" (a point is not a path); a name with no price_path member is "—" (never a blank cell);
// the column survives the business-type lens; the group row spans the widened table.
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
  const member = (ticker: string, sid: string) => ({
    ticker, role: "core", security_id: sid, detail: null, authored_by: "operator_set",
  });
  const pathSig = (values: (number | null)[], note: string | null) => ({
    kind: "price_path", label: "Price path", metrics: [], events: [],
    series: [{ key: "close", label: "close", unit: "price", values }],
    basis: {
      source: "fact_price_eod", params: { bars: 90, lookback_days: 150 },
      bars_used: values.filter((v) => v != null).length,
      window_start: "2026-04-01", window_end: "2026-08-07", note,
    },
  });
  const gaps = (n: number): (number | null)[] => new Array(n).fill(null);
  const full = Array.from({ length: 90 }, (_, i) => 10 + i * 0.1); // rising: min first, max last
  const young = [...gaps(56), ...Array.from({ length: 34 }, (_, i) => 20 + i)]; // 34 real bars
  const point = [...gaps(89), 42]; // one close — a point, not a path
  return {
    thesis: {
      id: "t-nuc", name: "Nuclear Buildout", narrative: "n", ticker: null, segments: [],
      basket: [
        member("OKLO", "s-oklo"), member("FISN", "s-fisn"),
        member("NEWCO", "s-newco"), member("NOPE", "s-nope"),
      ],
      evidence: [], catalysts: [], kill_criteria: [], position: null,
    },
    // card undefined -> every row reads Quiet (honest while the call computes); the lens still works
    scored: {
      members: [
        scoredRow("s-oklo", "OKLO", "Oklo Inc.", {
          business_type: "nuclear_smr", business_supersector: "energy_utilities", market_cap: fig(5e9),
        }),
        scoredRow("s-fisn", "FISN", "Thin Co", {}),
        scoredRow("s-newco", "NEWCO", "Listed Yesterday Inc.", {}),
        scoredRow("s-nope", "NOPE", "No Tape Co", {}),
      ],
    },
    display: {
      members: [
        { security_id: "s-oklo", ticker: "OKLO", signals: [pathSig(full, null)] },
        { security_id: "s-fisn", ticker: "FISN", signals: [pathSig(young, "thin: 34/90 bars")] },
        { security_id: "s-newco", ticker: "NEWCO", signals: [pathSig(point, "thin: 1/90 bars")] },
        // NOPE: no display row at all (nothing ingested) — its cell must still read an honest "—"
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
      railOpen
      onRailChange={() => {}}
    />,
  );
}

const rowOf = (ticker: string) => screen.getByText(ticker).closest("tr") as HTMLElement;
const sparkCell = (ticker: string) => rowOf(ticker).querySelector("td.sparkc") as HTMLElement;

describe("Cockpit — the close-path sparkline column (Path)", () => {
  it("renders a plain NON-sortable Path header right after 1Y; the sortable neighbours keep their exact names", () => {
    renderCockpit();
    const th = screen.getByRole("columnheader", { name: "Path" });
    expect(th.querySelector("button")).toBeNull(); // no sort control…
    expect(th).not.toHaveAttribute("aria-sort"); // …and no sort state: a shape is not a number to rank
    const headers = [...(th.parentElement as HTMLElement).querySelectorAll("th")].map((h) =>
      h.textContent?.trim(),
    );
    expect(headers.indexOf("Path")).toBe(headers.indexOf("1Y") + 1);
    // the existing headers resolve by their EXACT accessible names and stay sortable
    for (const name of ["30d", "90d", "1Y", "RVOL 8D", "RVOL 20D", "Ins 30d", "Ins 90d"]) {
      const s = screen.getByRole("columnheader", { name });
      expect(s).toHaveAttribute("aria-sort", "none");
      expect(s.querySelector("button.th-sort")).not.toBeNull();
    }
  });

  it("a full 90-slot series draws ONE unbroken path from the left edge to 'now' at the right edge", () => {
    renderCockpit();
    const svg = within(rowOf("OKLO")).getByRole("img", { name: "price path, 90 bars" });
    const paths = svg.querySelectorAll("path");
    expect(paths).toHaveLength(1);
    const d = paths[0].getAttribute("d") as string;
    expect(d.startsWith("M0 15")).toBe(true); // the first (lowest) close, bottom-left
    expect(d.endsWith("L72 1")).toBe(true); // the last (highest) close, top-right
    expect(d.match(/L/g)).toHaveLength(89); // 90 points, 89 links
    expect(svg.querySelectorAll("circle")).toHaveLength(0);
    // the exact tape on hover (show the work, #6)
    expect((svg.parentElement as HTMLElement).title).toMatch(/^90 bars · through /);
  });

  it("a young (left-padded) series draws a SHORTER, right-aligned path — the gap breaks the line", () => {
    renderCockpit();
    const svg = within(rowOf("FISN")).getByRole("img", { name: "price path, 34 bars" });
    const paths = svg.querySelectorAll("path");
    expect(paths).toHaveLength(1);
    const d = paths[0].getAttribute("d") as string;
    expect(d.match(/L/g)).toHaveLength(33); // 34 points, 33 links — nothing across the 56 gap slots
    expect(parseFloat(d.slice(1))).toBeCloseTo((56 / 89) * 72, 0); // starts at slot 56, never at x=0
    expect(d.endsWith("L72 1")).toBe(true); // …and still reaches "now" at the right edge
    expect((svg.parentElement as HTMLElement).title).toContain("thin: 34/90 bars"); // thinness named
  });

  it("a single close reads an honest '—' with the why on hover (a point is not a path)", () => {
    renderCockpit();
    const cell = sparkCell("NEWCO");
    expect(within(cell).queryByRole("img")).toBeNull();
    const dash = cell.querySelector("span") as HTMLElement;
    expect(dash.textContent).toBe("—");
    expect(dash.className).toContain("muted");
    expect(dash.title).toBe("thin: 1/90 bars");
  });

  it("a name with no price_path member reads '—' (never a blank cell, never a dropped row)", () => {
    renderCockpit();
    const cell = sparkCell("NOPE");
    expect(cell.textContent).toBe("—");
    expect(within(cell).queryByRole("img")).toBeNull();
  });

  it("keeps the column in the business-type lens and spans the group row across all 17 columns", () => {
    const { container } = renderCockpit();
    fireEvent.click(screen.getByRole("button", { name: "business type" }));
    expect(screen.getByRole("columnheader", { name: "Path" })).toBeInTheDocument();
    expect(within(rowOf("OKLO")).getByRole("img", { name: "price path, 90 bars" })).toBeInTheDocument();
    const grpCell = container.querySelector("tr.grp > td") as HTMLTableCellElement;
    expect(grpCell.colSpan).toBe(17);
  });
});
