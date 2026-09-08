import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DisplayHeadline, DisplaySignal, MemberDisplaySignalsOut } from "../../api/hooks";
import {
  DisplayHeadlineRow,
  DisplaySignalsSection,
  fmtMetricValue,
  ReturnCells,
  sparkGeometry,
  SparklineCell,
} from "../DisplaySignalsSection";

// One member's readings, exercising every unit the wire can carry plus an honest gap — the section
// must render ANY registered member off the generic payload (no per-kind frontend code).
const member = {
  security_id: "s-1",
  ticker: "HIMS",
  signals: [
    {
      kind: "sma_position",
      label: "SMA position (50/200d)",
      headline: {
        key: "below_rising",
        label: "50d under 200d · rising",
        glyph: "turn_up",
        detail: "price above both · rising",
      },
      metrics: [
        { key: "close", label: "close", value: 27.76, unit: "price", note: null },
        { key: "pct_vs_sma50", label: "vs 50d", value: 13.86, unit: "pct", note: null },
        { key: "pct_vs_sma200", label: "vs 200d", value: -19.14, unit: "pct", note: null },
        { key: "sma200", label: "200d SMA", value: null, unit: "price", note: "n/a: 140/200 bars" },
      ],
      events: [
        {
          key: "cross_sma50",
          label: "price crossed above 50d SMA",
          date: "2026-05-27",
          direction: "up",
        },
        {
          key: "death_cross",
          label: "death cross: 50d crossed below 200d",
          date: "2026-02-10",
          direction: "down",
        },
      ],
      basis: {
        source: "fact_price_eod",
        params: { fast: 50, slow: 200, lookback_days: 600 },
        bars_used: 248,
        window_start: "2025-06-05",
        window_end: "2026-06-01",
        note: "stale: last bar 14d before asof",
      },
    },
  ],
} as unknown as MemberDisplaySignalsOut;

describe("DisplaySignalsSection — the quiet Indicators block", () => {
  it("renders metric chips with unit formatting and the honest gap note", () => {
    render(<DisplaySignalsSection display={member} />);
    expect(screen.getByText("Indicators · this name")).toBeInTheDocument();
    expect(screen.getByText("SMA position (50/200d)")).toBeInTheDocument();
    expect(screen.getByText("27.76")).toBeInTheDocument(); // price, 2dp
    expect(screen.getByText("+13.9%")).toBeInTheDocument(); // pct, signed
    expect(screen.getByText("-19.1%")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument(); // the gap shows, it never fakes a number
    expect(screen.getByText("n/a: 140/200 bars")).toBeInTheDocument(); // …and says WHY (#6/#7)
  });

  it("renders each event with its direction glyph and date, and the basis fine print", () => {
    const { container } = render(<DisplaySignalsSection display={member} />);
    expect(screen.getByText("price crossed above 50d SMA")).toBeInTheDocument();
    expect(screen.getByText("death cross: 50d crossed below 200d")).toBeInTheDocument();
    expect(container.querySelector(".np-ind-event .dir.up")?.textContent).toBe("↑");
    expect(container.querySelectorAll(".np-ind-event .dir.down")[0]?.textContent).toBe("↓");
    // the show-the-work line: bars + through-date + the staleness tell, params on the hover title
    const basis = container.querySelector(".np-ind-basis") as HTMLElement;
    expect(basis.textContent).toMatch(/248 bars · through .* · stale: last bar 14d before asof/);
    expect(basis.title).toContain("fact_price_eod");
    expect(basis.title).toContain('"lookback_days":600');
  });

  it("DisplayHeadlineRow: tinted glyph, literal label, muted detail, key on hover", () => {
    const headline = member.signals![0].headline as DisplayHeadline;
    const { container } = render(<DisplayHeadlineRow headline={headline} />);
    const h = container.querySelector(".np-ind-headline") as HTMLElement;
    expect(h.querySelector(".g")?.textContent).toBe("↗");
    expect(h.querySelector(".g")?.className).toContain("turn_up"); // the tint class (glyph only)
    expect(screen.getByText("50d under 200d · rising")).toBeInTheDocument();
    expect(screen.getByText("price above both · rising")).toBeInTheDocument();
    expect(h.title).toBe("below_rising"); // the stable machine key rides the hover
  });

  it("the section itself never renders the headline — it is hoisted to the panel's top strip", () => {
    const { container } = render(<DisplaySignalsSection display={member} />);
    expect(container.querySelector(".np-ind-headline")).toBeNull();
    expect(container.querySelector(".np-ind-label")?.textContent).toBe("SMA position (50/200d)");
  });

  it("degrades to one muted line on empty signals and on a missing member row", () => {
    const { rerender } = render(<DisplaySignalsSection display={{ ...member, signals: [] }} />);
    expect(screen.getByText("No indicator data at this as-of.")).toBeInTheDocument();
    rerender(<DisplaySignalsSection display={null} />);
    expect(screen.getByText("No indicator data at this as-of.")).toBeInTheDocument();
  });

  it("injects a muted foreign-filer insider N/A when foreign + no insider signal (§16-exempt)", () => {
    // a foreign filer with only tape signals (no insider signal — it files no Form 4): the ambient N/A
    // explains the STRUCTURAL absence, reusing the .na muted styling (#7). The panel's SMA block stays.
    render(<DisplaySignalsSection display={member} foreignFilerForm="40-F" />);
    const na = screen.getByText("N/A — foreign filer (40-F), no Form 4 (§16-exempt)");
    expect(na).toBeInTheDocument();
    expect(screen.getByText(/§16 exempts foreign private issuers/)).toBeInTheDocument();
    // the N/A rides the muted .v.na value styling (#7 — ambient, never loud), inside an np-ind-chip
    expect(na.className).toContain("na");
    expect(na.closest(".np-ind-chip")).not.toBeNull();
  });

  it("shows the N/A even with NO tape data (and suppresses the empty 'no data' line)", () => {
    render(
      <DisplaySignalsSection display={{ ...member, signals: [] }} foreignFilerForm="20-F" />,
    );
    expect(
      screen.getByText("N/A — foreign filer (20-F), no Form 4 (§16-exempt)"),
    ).toBeInTheDocument();
    // the N/A is the honest content — the "no indicator data" empty line does NOT also render
    expect(screen.queryByText("No indicator data at this as-of.")).toBeNull();
  });

  it("renders NOTHING foreign-filer-related for a domestic name (no foreignFilerForm)", () => {
    render(<DisplaySignalsSection display={{ ...member, signals: [] }} />);
    expect(screen.queryByText(/no Form 4/)).toBeNull();
    expect(screen.getByText("No indicator data at this as-of.")).toBeInTheDocument();
  });

  it("does NOT inject the N/A when an insider signal IS present (belt-and-suspenders)", () => {
    const withInsider = {
      security_id: "s-x",
      ticker: "X",
      signals: [
        {
          kind: "insider_flow_90d",
          label: "Insider flow (90d)",
          metrics: [],
          events: [],
          basis: { source: "fact_insider_txn", params: {} },
        },
      ],
    } as unknown as MemberDisplaySignalsOut;
    render(<DisplaySignalsSection display={withInsider} foreignFilerForm="20-F" />);
    expect(screen.queryByText(/no Form 4/)).toBeNull();
  });

  it("fmtMetricValue covers every wire unit (a new member needs zero frontend change)", () => {
    const m = (value: number | null, unit: string | null) =>
      ({ key: "k", label: "l", value, unit, note: null }) as Parameters<typeof fmtMetricValue>[0];
    expect(fmtMetricValue(m(0.5, "pct"))).toBe("+0.5%");
    expect(fmtMetricValue(m(-19.14, "pct"))).toBe("-19.1%");
    expect(fmtMetricValue(m(24.375, "price"))).toBe("24.38");
    expect(fmtMetricValue(m(1_250_000, "usd"))).toBe("$1.3M");
    expect(fmtMetricValue(m(1.062, "ratio"))).toBe("1.06×");
    expect(fmtMetricValue(m(3.0, "count"))).toBe("3");
    expect(fmtMetricValue(m(7.5, null))).toBe("7.5"); // unitless: raw, never invented formatting
    expect(fmtMetricValue(m(null, "pct"))).toBe("—");
  });
});

describe("ReturnCells — the trailing-return table cells (1d/7d/30d/90d/1Y)", () => {
  // one name's trailing_returns member: up 1d, down 7d, a real flat 30d, a thin-history 90d gap, up 1Y
  const trailSig = {
    kind: "trailing_returns",
    label: "Trailing returns",
    metrics: [
      { key: "ret_1d", label: "1d", value: 2.6, unit: "pct", tone: "pos", note: null },
      { key: "ret_7d", label: "7d", value: -12.3, unit: "pct", tone: "neg", note: null },
      { key: "ret_30d", label: "30d", value: 0.0, unit: "pct", tone: null, note: null },
      { key: "ret_90d", label: "90d", value: null, unit: "pct", tone: null, note: "n/a: 34/91 bars" },
      { key: "ret_1y", label: "1Y", value: 55.0, unit: "pct", tone: "pos", note: null },
    ],
    basis: { source: "fact_price_eod", params: {} },
  } as unknown as DisplaySignal;

  const renderCells = (sig: DisplaySignal | null) =>
    render(
      <table>
        <tbody>
          <tr>
            <ReturnCells sig={sig} />
          </tr>
        </tbody>
      </table>,
    );

  it("renders five cells: signed % tinted green/red, a neutral flat 0, and an honest em-dash gap", () => {
    const { container } = renderCells(trailSig);
    expect(container.querySelectorAll("td.retc")).toHaveLength(5);

    const up = screen.getByText("+2.6%"); // green — the same +/- format the panel chips use
    expect(up.className).toContain("pos");
    expect(up.className).not.toContain("neg");
    const down = screen.getByText("-12.3%"); // red
    expect(down.className).toContain("neg");
    expect(down.className).not.toContain("pos");
    expect(screen.getByText("+55.0%").className).toContain("pos"); // the 1Y cell renders like the rest

    // a flat 0.0% move is neutral — the sign didn't move, so it's neither green nor red (#7)
    const flat = screen.getByText("0.0%");
    expect(flat.className).toContain("ret");
    expect(flat.className).not.toMatch(/pos|neg/);

    // the 90d gap: an HONEST em-dash with the "why" on hover, never a fabricated or zero number (#6/#9)
    const dash = screen.getByText("—");
    expect(dash.className).toContain("muted");
    expect(dash.title).toBe("n/a: 34/91 bars");
  });

  it("renders five em-dash cells when the name has no trailing signal at all (no bars)", () => {
    const { container } = renderCells(null);
    expect(container.querySelectorAll("td.retc")).toHaveLength(5);
    expect(screen.getAllByText("—")).toHaveLength(5); // never a blank/zero cell — always the honest dash
  });
});

// -------- the close-path sparkline (the price_path member's fixed-slot `close` series) ---------------
// The geometry is pure (hand-computed points on a 72×16 box); the cell is the honest-degrade contract:
// a null slot BREAKS the line (never interpolated), a young tape is a shorter right-aligned path, a
// single point is "—" (a point is not a path), and the shape stays NEUTRAL (no accent class).
describe("sparkGeometry — the pure sparkline geometry (a null slot BREAKS the line)", () => {
  it("maps slots across the whole window and values min → bottom / max → top", () => {
    const g = sparkGeometry([1, 3, 2], 72, 16);
    expect(g).not.toBeNull();
    expect(g!.bars).toBe(3);
    expect(g!.dots).toEqual([]);
    // x = 0/36/72 (3 slots over 72px); y: min 1 → 15 (bottom, 1px pad), max 3 → 1 (top), 2 → 8 (mid)
    expect(g!.paths).toEqual(["M0 15 L36 1 L72 8"]);
  });

  it("a leading gap starts the path at the first REAL slot — right-aligned, never stretched", () => {
    const g = sparkGeometry([null, null, 5, 6], 72, 16)!;
    expect(g.paths).toEqual(["M48 15 L72 1"]); // slot 2 of 4 → x=48; the two gaps draw nothing
    expect(g.bars).toBe(2);
  });

  it("a mid-series gap splits the line into TWO paths — nothing is drawn across it", () => {
    const g = sparkGeometry([1, 2, null, 2, 1], 72, 16)!;
    expect(g.paths).toEqual(["M0 15 L18 1", "M54 1 L72 15"]); // the x=36 slot is left empty
    expect(g.dots).toEqual([]);
  });

  it("an isolated bar between gaps is a dot — shown, never joined to a neighbour across a gap", () => {
    const g = sparkGeometry([1, null, 3, null, 2], 72, 16)!;
    expect(g.paths).toEqual([]);
    expect(g.dots).toEqual([
      { x: 0, y: 15 },
      { x: 36, y: 1 },
      { x: 72, y: 8 },
    ]);
  });

  it("fewer than two real values is NOT a path (null) — the cell reads '—'", () => {
    expect(sparkGeometry([null, null, 42])).toBeNull();
    expect(sparkGeometry([null, null])).toBeNull();
    expect(sparkGeometry([])).toBeNull();
  });

  it("a flat series draws a midline, never a zero-range blow-up", () => {
    expect(sparkGeometry([5, 5, 5], 72, 16)!.paths).toEqual(["M0 8 L36 8 L72 8"]);
  });
});

describe("SparklineCell — the basket-table close-path cell", () => {
  const pathSig = (values: (number | null)[], note: string | null = null) =>
    ({
      kind: "price_path",
      label: "Price path",
      metrics: [],
      events: [],
      series: [{ key: "close", label: "close", unit: "price", values }],
      basis: {
        source: "fact_price_eod",
        params: { bars: 90, lookback_days: 150 },
        bars_used: values.filter((v) => v != null).length,
        window_start: "2026-04-01",
        window_end: "2026-08-07",
        note,
      },
    }) as unknown as DisplaySignal;

  it("draws a NEUTRAL hairline path — no return/RVOL/insider accent — with the exact tape on hover", () => {
    const { container } = render(<SparklineCell sig={pathSig([10, 11, 12, 11.5])} />);
    const svg = screen.getByRole("img", { name: "price path, 4 bars" });
    expect(svg).toHaveAttribute("class", "spark-svg");
    expect(svg.querySelectorAll("path")).toHaveLength(1);
    expect(svg.querySelectorAll("circle")).toHaveLength(0);
    // the shape carries none of the other columns' accent classes (#7)
    expect(container.querySelector(".pos, .neg, .hot, .cluster")).toBeNull();
    // show-the-work rides the hover: bars + through-date (the basis line the panel prints, #6)
    expect((svg.parentElement as HTMLElement).title).toMatch(/^4 bars · through /);
  });

  it("a leading-null (young) series draws a shorter path that starts mid-cell and names its thinness", () => {
    const values: (number | null)[] = [null, null, null, null, null, null, 7, 8, 9, 10];
    render(<SparklineCell sig={pathSig(values, "thin: 4/10 bars")} />);
    const svg = screen.getByRole("img", { name: "price path, 4 bars" });
    const d = svg.querySelector("path")!.getAttribute("d") as string;
    expect(d.startsWith("M48 15")).toBe(true); // slot 6 of 10 → x = 6/9·72 = 48, never x = 0
    expect(d.endsWith("L72 1")).toBe(true); // …and reaches "now" at the right edge
    expect(d.match(/L/g)).toHaveLength(3); // 4 points, 3 links — nothing drawn across the gaps
    expect((svg.parentElement as HTMLElement).title).toContain("thin: 4/10 bars");
  });

  it("reads '—' with the why on hover for a single point (a point is not a path)", () => {
    render(<SparklineCell sig={pathSig([null, null, 42], "thin: 1/90 bars")} />);
    expect(screen.queryByRole("img")).toBeNull();
    const dash = screen.getByText("—");
    expect(dash.className).toContain("muted");
    expect(dash.title).toBe("thin: 1/90 bars");
  });

  it("reads '—' when the name has no price_path signal, or the signal carries no close series", () => {
    const { rerender } = render(<SparklineCell sig={null} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    rerender(<SparklineCell sig={{ ...pathSig([1, 2]), series: [] } as DisplaySignal} />);
    expect(screen.getByText("—")).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });
});
