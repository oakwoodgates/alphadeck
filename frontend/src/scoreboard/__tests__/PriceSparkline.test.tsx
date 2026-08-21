import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { InsiderBuyOut, PriceBar, ScoreboardEpisodeOut, TriggerRefOut } from "../../api/hooks";
import { buildOverlayEvents } from "../overlay";

// The drawer chart (Slice A/R1, lifted onto shared props in Slice B): honest loudness (a no-forward-bar or
// thin series draws NO chart, a quiet line instead; an error/loading line), the close LINE + faint SMA
// 50/200, a custom numbered-chip overlay (hoverable, with a guide-line + price-context tooltip), a
// present-families legend, the DEFAULT episode visible range, R5 hide/clamp at the edges, and the Slice B
// cross-highlight (a chip hover reports up; an activeN prop rings the match WITHOUT rebuilding the canvas).
// lightweight-charts is mocked (canvas doesn't run in jsdom). The fetch + numbering now live in the parent,
// so this component takes `bars` + `events` as props — no hook to stub. Positioning + pan/zoom are
// live-verified by eye; here we assert the lib contract + DOM.

const lw = vi.hoisted(() => {
  const series = { setData: vi.fn(), setMarkers: vi.fn(), priceToCoordinate: vi.fn(() => 80) };
  const histSeries = { setData: vi.fn(), applyOptions: vi.fn() }; // A2: the volume histogram
  const priceScale = { applyOptions: vi.fn() }; // A2: the "vol" overlay scale
  const timeScale = {
    setVisibleRange: vi.fn(),
    fitContent: vi.fn(),
    timeToCoordinate: vi.fn(),
    subscribeVisibleTimeRangeChange: vi.fn(),
    unsubscribeVisibleTimeRangeChange: vi.fn(),
  };
  const chart = {
    addLineSeries: vi.fn(() => series),
    addHistogramSeries: vi.fn(() => histSeries),
    priceScale: vi.fn(() => priceScale),
    timeScale: vi.fn(() => timeScale),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  };
  return { series, histSeries, priceScale, timeScale, chart, createChart: vi.fn(() => chart) };
});
vi.mock("lightweight-charts", () => ({
  createChart: lw.createChart,
  ColorType: { Solid: "solid" },
}));

import { PriceSparkline } from "../PriceSparkline";

function ep(over: Partial<ScoreboardEpisodeOut> = {}): ScoreboardEpisodeOut {
  return {
    thesis_id: "t1",
    security_id: "s1",
    arm_date: "2026-06-05",
    warm_date: "2026-06-02",
    dearm_date: null,
    close_reason: "",
    exit_by: "2026-06-20",
    peak_date: null,
    truncated: false,
    insufficient_prices: false,
    exit_date: "2026-06-15",
    triggers_at_arm: [] as TriggerRefOut[],
    ...over,
  } as unknown as ScoreboardEpisodeOut;
}

function bar(d: string, close: number, sma50: number | null = null, sma200: number | null = null) {
  return { d, open: null, high: null, low: null, close, volume: null, sma50, sma200 };
}

function buy(d: string, over: Partial<InsiderBuyOut> = {}): InsiderBuyOut {
  return { d, insider_name: "A Buyer", insider_role: "CEO", shares: 1000, usd: 50000, aff_10b5_1: false, disclosed: d, ingested: d, character: "open_market", ...over };
}

const TRIG = { label: "3 insiders bought", kind: "insider", ticker: "IBM" } as TriggerRefOut;

// six bars 06-01..06-15; SMA null early (the honest gap), values late
const BARS = [
  bar("2026-06-01", 100),
  bar("2026-06-02", 101),
  bar("2026-06-03", 102),
  bar("2026-06-05", 104, 103),
  bar("2026-06-08", 107, 105),
  bar("2026-06-15", 110, 108),
];

// day-of-month × 30 → distinct x per date; overridden in edge-case tests
const coord = (t: string) => parseInt(String(t).slice(-2), 10) * 30;

/** Render the chart with props (the parent's job — the fetch and numbering — done here in the test): build
 *  the SAME `events` the production parent would via buildOverlayEvents, then pass bars + events down. */
function renderSpark(
  opts: {
    ep?: ScoreboardEpisodeOut;
    bars?: ReturnType<typeof bar>[];
    insiderBuys?: InsiderBuyOut[];
    activeN?: number | null;
    loading?: boolean;
    error?: boolean;
    onActivate?: (n: number | null) => void;
  } = {},
) {
  const e = opts.ep ?? ep();
  const bars = opts.bars ?? BARS;
  const events = buildOverlayEvents(e, opts.insiderBuys ?? [], bars);
  const onActivate = opts.onActivate ?? vi.fn();
  const utils = render(
    <PriceSparkline
      ep={e}
      bars={bars as unknown as PriceBar[]}
      events={events}
      activeN={opts.activeN ?? null}
      onActivate={onActivate}
      loading={opts.loading ?? false}
      error={opts.error ?? false}
    />,
  );
  return { onActivate, events, ...utils };
}

beforeEach(() => {
  lw.createChart.mockClear();
  lw.chart.addLineSeries.mockClear();
  lw.chart.addHistogramSeries.mockClear();
  lw.chart.priceScale.mockClear();
  lw.chart.remove.mockClear();
  lw.series.setData.mockClear();
  lw.series.setMarkers.mockClear();
  lw.series.priceToCoordinate.mockClear();
  lw.histSeries.setData.mockClear();
  lw.histSeries.applyOptions.mockClear();
  lw.priceScale.applyOptions.mockClear();
  lw.timeScale.setVisibleRange.mockClear();
  lw.timeScale.subscribeVisibleTimeRangeChange.mockClear();
  lw.timeScale.unsubscribeVisibleTimeRangeChange.mockClear();
  lw.timeScale.timeToCoordinate.mockReset();
  lw.timeScale.timeToCoordinate.mockImplementation(coord);
});

describe("PriceSparkline — honest loudness", () => {
  it("a no-forward-bar episode WITH a price path still draws its (pre-arm) chart", () => {
    // the window is the name's full history, not arm-relative — a just-armed episode has months of pre-arm
    // path to draw; only the forward-OUTCOME lenses stay gated (covered in EpisodeScorecard's tests)
    renderSpark({ ep: ep({ insufficient_prices: true }), bars: BARS });
    expect(lw.createChart).toHaveBeenCalled();
    expect(screen.queryByText("no price path yet")).toBeNull();
  });

  it("a single-bar (thin) series draws NO chart, just the quiet line", () => {
    renderSpark({ bars: [bar("2026-06-01", 100)] });
    expect(screen.getByText("no price path yet")).toBeInTheDocument();
    expect(lw.createChart).not.toHaveBeenCalled();
  });

  it("an error surfaces a quiet 'price path unavailable', never a broken chart", () => {
    renderSpark({ bars: [], error: true });
    expect(screen.getByText("price path unavailable")).toBeInTheDocument();
    expect(lw.createChart).not.toHaveBeenCalled();
  });

  it("a still-loading window (no bars yet) reads 'reading the price path…'", () => {
    renderSpark({ bars: [], loading: true });
    expect(screen.getByText("reading the price path…")).toBeInTheDocument();
    expect(lw.createChart).not.toHaveBeenCalled();
  });
});

describe("PriceSparkline — the close line + SMA context + default view", () => {
  it("draws SMA200 + SMA50 behind the close line; SMA nulls are filtered (the honest gap)", () => {
    renderSpark();
    expect(lw.chart.addLineSeries).toHaveBeenCalledTimes(3); // sma200, sma50, close
    expect(lw.series.setData.mock.calls[0][0]).toEqual([]); // no sma200 → an empty context line
    expect(lw.series.setData.mock.calls[1][0]).toHaveLength(3); // only the bars that HAVE an sma50
    expect(lw.series.setData.mock.calls[2][0]).toHaveLength(6); // the full close line
  });

  it("defaults the visible range to the episode (R2), not fitContent-to-all", () => {
    renderSpark();
    // arm 06-05 (index 3), 3 − 130 < 0 → from clamps to the first bar; to = last bar
    expect(lw.timeScale.setVisibleRange).toHaveBeenCalledWith({ from: "2026-06-01", to: "2026-06-15" });
  });

  it("disposes the chart on unmount (no leak) and unsubscribes", () => {
    const { unmount } = renderSpark();
    expect(lw.chart.remove).not.toHaveBeenCalled();
    unmount();
    expect(lw.chart.remove).toHaveBeenCalledTimes(1);
    expect(lw.timeScale.unsubscribeVisibleTimeRangeChange).toHaveBeenCalledTimes(1);
  });
});

describe("PriceSparkline — the numbered-chip overlay", () => {
  it("renders a stable numbered chip per recorded event (insider / trigger / lifecycle)", () => {
    renderSpark({
      ep: ep({ triggers_at_arm: [TRIG] }),
      insiderBuys: [buy("2026-06-03"), buy("2026-06-08", { insider_name: "B Buyer" })],
    });
    // warmed 06-02, insider 06-03, armed 06-05, trigger 06-05, insider 06-08 (exit-by 06-20 > last → dropped)
    for (const n of ["1", "2", "3", "4", "5"]) {
      expect(screen.getByRole("button", { name: new RegExp(`event ${n}:`) })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: /event 6:/ })).not.toBeInTheDocument();
  });

  it("renders a legend of ONLY the families present; wires pan/zoom repositioning (R4)", () => {
    renderSpark({ ep: ep({ triggers_at_arm: [TRIG] }), insiderBuys: [buy("2026-06-03")] });
    expect(screen.getByText("insider buy")).toBeInTheDocument();
    expect(screen.getByText("arm trigger")).toBeInTheDocument();
    expect(screen.getByText("lifecycle")).toBeInTheDocument();
    // the chip layer subscribes to visible-range changes → chips track pan/zoom
    expect(lw.timeScale.subscribeVisibleTimeRangeChange).toHaveBeenCalled();
    const onRange = lw.timeScale.subscribeVisibleTimeRangeChange.mock.calls[0][0];
    expect(() => act(() => onRange())).not.toThrow(); // a pan re-runs reposition without crashing
  });

  it("omits a family with zero events from the legend", () => {
    renderSpark({ ep: ep({ triggers_at_arm: [] }), insiderBuys: [] });
    expect(screen.getByText("lifecycle")).toBeInTheDocument();
    expect(screen.queryByText("insider buy")).not.toBeInTheDocument();
    expect(screen.queryByText("arm trigger")).not.toBeInTheDocument();
  });

  it("a SET-ASIDE buy renders its chip greyed (ov-setaside), present, never dropped (S2c — WB #2)", () => {
    // NB the CSS class is the jsdom-provable half; the actual grey needs the live-eye on dev
    renderSpark({
      insiderBuys: [
        buy("2026-06-03", { character: "primary_market" }),
        buy("2026-06-08", { insider_name: "B Buyer" }),
      ],
    });
    const setAside = screen.getByRole("button", { name: /A Buyer/ });
    expect(setAside).toHaveClass("ov-setaside"); // greyed…
    expect(setAside).toHaveClass("ov-insider"); // …but still the insider family (hue + legend)
    expect(screen.getByRole("button", { name: /B Buyer/ })).not.toHaveClass("ov-setaside");
  });

  it("hides a chip whose bar is off the visible range (R5)", () => {
    lw.timeScale.timeToCoordinate.mockImplementation((t: string) => (t === "2026-06-08" ? null : coord(t)));
    renderSpark({ insiderBuys: [buy("2026-06-03"), buy("2026-06-08", { insider_name: "B Buyer" })] });
    expect(screen.getByRole("button", { name: /A Buyer/ })).toBeInTheDocument(); // 06-03 in view
    expect(screen.queryByRole("button", { name: /B Buyer/ })).not.toBeInTheDocument(); // 06-08 off view → hidden
  });

  it("clamps a chip's x so the whole chip stays on-screen at the edge (R5)", () => {
    lw.timeScale.timeToCoordinate.mockImplementation((t: string) => (t === "2026-06-03" ? 5000 : coord(t)));
    renderSpark({ insiderBuys: [buy("2026-06-03")] });
    const chip = screen.getByRole("button", { name: /A Buyer/ });
    // plot width falls back to 320 in jsdom → clamp to 320 − 11 = 309px (fully on-screen)
    expect(chip).toHaveStyle({ left: "309px" });
  });

  it("shows a tooltip + guide-line on hover, with the market-price context (R3)", () => {
    const { container } = renderSpark({ insiderBuys: [buy("2026-06-03", { insider_name: "Jane Doe" })] });
    const chip = screen.getByRole("button", { name: /Jane Doe/ });
    fireEvent.mouseEnter(chip);
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("Jane Doe (CEO)");
    // close on 2026-06-03 = 102; last close 110 → +8% vs now
    expect(tip).toHaveTextContent("stock $102 that day · +8% vs now");
    expect(lw.series.priceToCoordinate).toHaveBeenCalledWith(102); // guide-line foot = the close that day
    expect(container.querySelector(".ov-guide")).not.toBeNull();
    fireEvent.mouseLeave(chip);
    expect(container.querySelector(".ov-guide")).toBeNull(); // removed on un-hover
  });
});

describe("PriceSparkline — the Slice B cross-highlight", () => {
  it("a chip hover reports its number up (onActivate) and clears on leave", () => {
    const { onActivate } = renderSpark({ insiderBuys: [buy("2026-06-03")] });
    const chip = screen.getByRole("button", { name: /A Buyer/ }); // insider #2
    fireEvent.mouseEnter(chip);
    expect(onActivate).toHaveBeenCalledWith(2);
    fireEvent.mouseLeave(chip);
    expect(onActivate).toHaveBeenLastCalledWith(null);
  });

  it("the activeN prop rings the matching chip WITHOUT rebuilding the canvas (anti-storm invariant)", () => {
    // stable prop references so a re-render with a new activeN cannot re-run the chart-building effect
    const e = ep();
    const bars = BARS as unknown as PriceBar[];
    const events = buildOverlayEvents(e, [buy("2026-06-03")], BARS);
    const props = { ep: e, bars, events, onActivate: vi.fn(), loading: false, error: false };
    const { rerender } = render(<PriceSparkline {...props} activeN={null} />);
    expect(lw.createChart).toHaveBeenCalledTimes(1);
    const chip = screen.getByRole("button", { name: /A Buyer/ });
    expect(chip).not.toHaveClass("active");

    rerender(<PriceSparkline {...props} activeN={2} />); // a ledger-row hover flips activeN
    expect(screen.getByRole("button", { name: /A Buyer/ })).toHaveClass("active");
    expect(lw.createChart).toHaveBeenCalledTimes(1); // NOT 2 — activeN is not an effect dep
  });
});

// -------- Slice A2: outcome markers + the expanded-mode volume histogram --------------------------------
// The selection (episodeMarkers) and the null-volume skip (volumeData) are overlay.test.ts's; here we
// assert the LIB CONTRACT — what reaches setMarkers / addHistogramSeries. The expanded-mode visibility
// FLIP rides the ResizeObserver (absent in jsdom, guarded) → live-verified by eye, per the module rule.
describe("PriceSparkline — Slice A2: outcome markers + volume", () => {
  it("sets the un-numbered outcome markers on the close series — labels, glyphs, muted colors", () => {
    renderSpark({
      ep: ep({ entry_close: 104, exit_close: 110, exit_date: "2026-06-15", peak_date: "2026-06-08" }),
    });
    expect(lw.series.setMarkers).toHaveBeenCalledWith([
      { time: "2026-06-05", position: "belowBar", shape: "arrowUp", text: "entry", color: "#5a6470", size: 1 },
      { time: "2026-06-08", position: "aboveBar", shape: "circle", text: "peak", color: "#586374", size: 1 },
      { time: "2026-06-15", position: "aboveBar", shape: "arrowDown", text: "exit", color: "#5a6470", size: 1 },
    ]);
  });

  it("markers are NOT chips: they add no numbered buttons to the overlay (derived, un-numbered)", () => {
    renderSpark({
      ep: ep({ entry_close: 104, exit_close: 110, exit_date: "2026-06-15", peak_date: "2026-06-08" }),
    });
    // the numbered universe is untouched: warmed + armed only (exit-by 06-20 sits past the last bar)
    expect(screen.getAllByRole("button")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /entry|exit|peak/ })).toBeNull();
  });

  it("an episode with no priced outcome sets NO markers — nothing invented (#6)", () => {
    renderSpark(); // the default ep carries no entry_close/exit_close/peak_date
    expect(lw.series.setMarkers).toHaveBeenCalledWith([]);
  });

  it("creates the volume histogram on its OWN 'vol' overlay scale (the close pin never sees it)", () => {
    const vbars = BARS.map((b, i) => ({ ...b, volume: i === 1 ? null : (i + 1) * 1000 }));
    renderSpark({ bars: vbars });
    expect(lw.chart.addHistogramSeries).toHaveBeenCalledWith(
      expect.objectContaining({ priceScaleId: "vol", visible: false }), // jsdom width 320 → inline → hidden
    );
    expect(lw.chart.priceScale).toHaveBeenCalledWith("vol");
    expect(lw.priceScale.applyOptions).toHaveBeenCalledWith({
      scaleMargins: { top: 0.78, bottom: 0 }, // confined to the bottom register
    });
    // the null-volume bar (index 1) is a GAP, not a zero
    expect(lw.histSeries.setData).toHaveBeenCalledWith([
      { time: "2026-06-01", value: 1000 },
      { time: "2026-06-03", value: 3000 },
      { time: "2026-06-05", value: 4000 },
      { time: "2026-06-08", value: 5000 },
      { time: "2026-06-15", value: 6000 },
    ]);
  });

  it("the close-line autoscale pin is untouched by A2 — the close series still provides its fixed range", () => {
    renderSpark({ ep: ep({ entry_close: 104, exit_close: 110, exit_date: "2026-06-15" }) });
    // the LAST addLineSeries is the close: its provider returns the pinned range (the #229 fix) even
    // with markers set — in v4 the provider REPLACES the default computation where marker margins live
    const closeOpts = lw.chart.addLineSeries.mock.calls[2][0];
    const info = closeOpts.autoscaleInfoProvider();
    expect(info.priceRange.minValue).toBeLessThan(100); // lo − pad
    expect(info.priceRange.maxValue).toBeGreaterThan(110); // hi + pad
  });
});
