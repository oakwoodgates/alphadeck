import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DisplayHeadline, MemberDisplaySignalsOut } from "../../api/hooks";
import { DisplayHeadlineRow, DisplaySignalsSection, fmtMetricValue } from "../DisplaySignalsSection";

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
