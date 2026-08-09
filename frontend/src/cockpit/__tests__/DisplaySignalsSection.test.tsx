import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DisplayHeadline, DisplaySignal, MemberDisplaySignalsOut } from "../../api/hooks";
import {
  DisplayHeadlineRow,
  DisplaySignalsSection,
  fmtMetricValue,
  ReturnCells,
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

describe("ReturnCells — the trailing-return table cells (1d/7d/30d/90d)", () => {
  // one name's trailing_returns member: up 1d, down 7d, a real flat 30d, and a thin-history 90d gap
  const trailSig = {
    kind: "trailing_returns",
    label: "Trailing returns",
    metrics: [
      { key: "ret_1d", label: "1d", value: 2.6, unit: "pct", tone: "pos", note: null },
      { key: "ret_7d", label: "7d", value: -12.3, unit: "pct", tone: "neg", note: null },
      { key: "ret_30d", label: "30d", value: 0.0, unit: "pct", tone: null, note: null },
      { key: "ret_90d", label: "90d", value: null, unit: "pct", tone: null, note: "n/a: 34/91 bars" },
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

  it("renders four cells: signed % tinted green/red, a neutral flat 0, and an honest em-dash gap", () => {
    const { container } = renderCells(trailSig);
    expect(container.querySelectorAll("td.retc")).toHaveLength(4);

    const up = screen.getByText("+2.6%"); // green — the same +/- format the panel chips use
    expect(up.className).toContain("pos");
    expect(up.className).not.toContain("neg");
    const down = screen.getByText("-12.3%"); // red
    expect(down.className).toContain("neg");
    expect(down.className).not.toContain("pos");

    // a flat 0.0% move is neutral — the sign didn't move, so it's neither green nor red (#7)
    const flat = screen.getByText("0.0%");
    expect(flat.className).toContain("ret");
    expect(flat.className).not.toMatch(/pos|neg/);

    // the 90d gap: an HONEST em-dash with the "why" on hover, never a fabricated or zero number (#6/#9)
    const dash = screen.getByText("—");
    expect(dash.className).toContain("muted");
    expect(dash.title).toBe("n/a: 34/91 bars");
  });

  it("renders four em-dash cells when the name has no trailing signal at all (no bars)", () => {
    const { container } = renderCells(null);
    expect(container.querySelectorAll("td.retc")).toHaveLength(4);
    expect(screen.getAllByText("—")).toHaveLength(4); // never a blank/zero cell — always the honest dash
  });
});
