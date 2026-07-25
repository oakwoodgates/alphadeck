import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { InsiderBuyOut, ScoreboardEpisodeOut, TriggerRefOut } from "../../api/hooks";

// The drawer chart (Slice A, R1): honest loudness (a no-forward-bar or thin series draws NO chart, a quiet
// line instead), the close LINE + faint SMA 50/200, a custom numbered-chip overlay (hoverable, with a
// guide-line + price-context tooltip), a present-families legend, the DEFAULT episode visible range, and
// R5 hide/clamp at the edges. lightweight-charts is mocked (canvas doesn't run in jsdom); the price-window
// hook is stubbed. Positioning + pan/zoom are live-verified by eye — here we assert the lib contract + DOM.

const lw = vi.hoisted(() => {
  const series = { setData: vi.fn(), priceToCoordinate: vi.fn(() => 80) };
  const timeScale = {
    setVisibleRange: vi.fn(),
    fitContent: vi.fn(),
    timeToCoordinate: vi.fn(),
    subscribeVisibleTimeRangeChange: vi.fn(),
    unsubscribeVisibleTimeRangeChange: vi.fn(),
  };
  const chart = {
    addLineSeries: vi.fn(() => series),
    timeScale: vi.fn(() => timeScale),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  };
  return { series, timeScale, chart, createChart: vi.fn(() => chart) };
});
vi.mock("lightweight-charts", () => ({
  createChart: lw.createChart,
  ColorType: { Solid: "solid" },
}));

const h = vi.hoisted(() => ({ use: vi.fn() }));
vi.mock("../../api/hooks", () => ({ useEpisodePriceWindow: h.use }));

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
  return { d, insider_name: "A Buyer", insider_role: "CEO", shares: 1000, usd: 50000, aff_10b5_1: false, disclosed: d, ...over };
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

function windowData(over: { bars?: unknown; insider_buys?: InsiderBuyOut[] } = {}) {
  return {
    data: { bars: over.bars ?? BARS, insider_buys: over.insider_buys ?? [] },
    isLoading: false,
    isError: false,
  };
}

beforeEach(() => {
  lw.createChart.mockClear();
  lw.chart.addLineSeries.mockClear();
  lw.chart.remove.mockClear();
  lw.series.setData.mockClear();
  lw.series.priceToCoordinate.mockClear();
  lw.timeScale.setVisibleRange.mockClear();
  lw.timeScale.subscribeVisibleTimeRangeChange.mockClear();
  lw.timeScale.unsubscribeVisibleTimeRangeChange.mockClear();
  lw.timeScale.timeToCoordinate.mockReset();
  lw.timeScale.timeToCoordinate.mockImplementation(coord);
  h.use.mockReset();
});

describe("PriceSparkline — honest loudness", () => {
  it("a no-forward-bar episode draws NO chart, a quiet line, and never fetches", () => {
    h.use.mockReturnValue({ data: undefined, isLoading: false, isError: false });
    render(<PriceSparkline ep={ep({ insufficient_prices: true })} asof="2026-07-15" />);
    expect(screen.getByText("no price path yet")).toBeInTheDocument();
    expect(lw.createChart).not.toHaveBeenCalled();
    expect(h.use).toHaveBeenCalledWith(expect.objectContaining({ start: "2026-06-05" }), false);
  });

  it("a single-bar (thin) series draws NO chart, just the quiet line", () => {
    h.use.mockReturnValue(windowData({ bars: [bar("2026-06-01", 100)] }));
    render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
    expect(screen.getByText("no price path yet")).toBeInTheDocument();
    expect(lw.createChart).not.toHaveBeenCalled();
  });

  it("an error surfaces a quiet 'price path unavailable', never a broken chart", () => {
    h.use.mockReturnValue({ data: undefined, isLoading: false, isError: true });
    render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
    expect(screen.getByText("price path unavailable")).toBeInTheDocument();
    expect(lw.createChart).not.toHaveBeenCalled();
  });
});

describe("PriceSparkline — the close line + SMA context + default view", () => {
  it("requests with the arm_date as the advisory anchor (server owns the floor) and enables the query", () => {
    h.use.mockReturnValue(windowData());
    render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
    const [args, enabled] = h.use.mock.calls[0];
    expect(enabled).toBe(true);
    expect(args.start).toBe("2026-06-05"); // arm_date — advisory; the server returns [floor, end]
    expect(args.asof).toBe("2026-07-15");
  });

  it("draws SMA200 + SMA50 behind the close line; SMA nulls are filtered (the honest gap)", () => {
    h.use.mockReturnValue(windowData());
    render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
    expect(lw.chart.addLineSeries).toHaveBeenCalledTimes(3); // sma200, sma50, close
    expect(lw.series.setData.mock.calls[0][0]).toEqual([]); // no sma200 → an empty context line
    expect(lw.series.setData.mock.calls[1][0]).toHaveLength(3); // only the bars that HAVE an sma50
    expect(lw.series.setData.mock.calls[2][0]).toHaveLength(6); // the full close line
  });

  it("defaults the visible range to the episode (R2), not fitContent-to-all", () => {
    h.use.mockReturnValue(windowData());
    render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
    // arm 06-05 (index 3), 3 − 130 < 0 → from clamps to the first bar; to = last bar
    expect(lw.timeScale.setVisibleRange).toHaveBeenCalledWith({ from: "2026-06-01", to: "2026-06-15" });
  });

  it("disposes the chart on unmount (no leak) and unsubscribes", () => {
    h.use.mockReturnValue(windowData());
    const { unmount } = render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
    expect(lw.chart.remove).not.toHaveBeenCalled();
    unmount();
    expect(lw.chart.remove).toHaveBeenCalledTimes(1);
    expect(lw.timeScale.unsubscribeVisibleTimeRangeChange).toHaveBeenCalledTimes(1);
  });
});

describe("PriceSparkline — the numbered-chip overlay", () => {
  it("renders a stable numbered chip per recorded event (insider / trigger / lifecycle)", () => {
    h.use.mockReturnValue(
      windowData({ insider_buys: [buy("2026-06-03"), buy("2026-06-08", { insider_name: "B Buyer" })] }),
    );
    render(<PriceSparkline ep={ep({ triggers_at_arm: [TRIG] })} asof="2026-07-15" />);
    // warmed 06-02, insider 06-03, armed 06-05, trigger 06-05, insider 06-08 (exit-by 06-20 > last → dropped)
    for (const n of ["1", "2", "3", "4", "5"]) {
      expect(screen.getByRole("button", { name: new RegExp(`event ${n}:`) })).toBeInTheDocument();
    }
    expect(screen.queryByRole("button", { name: /event 6:/ })).not.toBeInTheDocument();
  });

  it("renders a legend of ONLY the families present; wires pan/zoom repositioning (R4)", () => {
    h.use.mockReturnValue(windowData({ insider_buys: [buy("2026-06-03")] }));
    render(<PriceSparkline ep={ep({ triggers_at_arm: [TRIG] })} asof="2026-07-15" />);
    expect(screen.getByText("insider buy")).toBeInTheDocument();
    expect(screen.getByText("arm trigger")).toBeInTheDocument();
    expect(screen.getByText("lifecycle")).toBeInTheDocument();
    // the chip layer subscribes to visible-range changes → chips track pan/zoom
    expect(lw.timeScale.subscribeVisibleTimeRangeChange).toHaveBeenCalled();
    const onRange = lw.timeScale.subscribeVisibleTimeRangeChange.mock.calls[0][0];
    expect(() => act(() => onRange())).not.toThrow(); // a pan re-runs reposition without crashing
  });

  it("omits a family with zero events from the legend", () => {
    h.use.mockReturnValue(windowData({ insider_buys: [] }));
    render(<PriceSparkline ep={ep({ triggers_at_arm: [] })} asof="2026-07-15" />);
    expect(screen.getByText("lifecycle")).toBeInTheDocument();
    expect(screen.queryByText("insider buy")).not.toBeInTheDocument();
    expect(screen.queryByText("arm trigger")).not.toBeInTheDocument();
  });

  it("hides a chip whose bar is off the visible range (R5)", () => {
    lw.timeScale.timeToCoordinate.mockImplementation((t: string) => (t === "2026-06-08" ? null : coord(t)));
    h.use.mockReturnValue(
      windowData({ insider_buys: [buy("2026-06-03"), buy("2026-06-08", { insider_name: "B Buyer" })] }),
    );
    render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
    expect(screen.getByRole("button", { name: /A Buyer/ })).toBeInTheDocument(); // 06-03 in view
    expect(screen.queryByRole("button", { name: /B Buyer/ })).not.toBeInTheDocument(); // 06-08 off view → hidden
  });

  it("clamps a chip's x so the whole chip stays on-screen at the edge (R5)", () => {
    lw.timeScale.timeToCoordinate.mockImplementation((t: string) => (t === "2026-06-03" ? 5000 : coord(t)));
    h.use.mockReturnValue(windowData({ insider_buys: [buy("2026-06-03")] }));
    render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
    const chip = screen.getByRole("button", { name: /A Buyer/ });
    // plot width falls back to 320 in jsdom → clamp to 320 − 11 = 309px (fully on-screen)
    expect(chip).toHaveStyle({ left: "309px" });
  });

  it("shows a tooltip + guide-line on hover, with the market-price context (R3)", () => {
    h.use.mockReturnValue(windowData({ insider_buys: [buy("2026-06-03", { insider_name: "Jane Doe" })] }));
    const { container } = render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
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
