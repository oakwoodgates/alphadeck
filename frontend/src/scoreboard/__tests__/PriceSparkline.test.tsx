import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ScoreboardEpisodeOut } from "../../api/hooks";

// The drawer sparkline: honest loudness (a no-forward-bar or thin series draws NO chart, a quiet line
// instead), the close LINE drawn from the OHLCV bars, arm/peak/last markers, and disposal on unmount.
// lightweight-charts is mocked (canvas doesn't run in jsdom) and the price-window hook is stubbed, so the
// test asserts the component's contract with the chart lib, not the lib's rendering.

const lw = vi.hoisted(() => {
  const series = { setData: vi.fn(), setMarkers: vi.fn() };
  const chart = {
    addLineSeries: vi.fn(() => series),
    timeScale: vi.fn(() => ({ fitContent: vi.fn() })),
    applyOptions: vi.fn(),
    remove: vi.fn(),
  };
  return { series, chart, createChart: vi.fn(() => chart) };
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
    arm_date: "2026-06-01",
    exit_by: "2026-09-01",
    peak_date: null,
    truncated: false,
    insufficient_prices: false,
    exit_date: "2026-07-15", // != arm_date → not a single-arm-bar episode
    ...over,
  } as unknown as ScoreboardEpisodeOut;
}

function bar(d: string, close: number) {
  return { d, open: null, high: null, low: null, close, volume: null };
}

beforeEach(() => {
  lw.createChart.mockClear();
  lw.chart.addLineSeries.mockClear();
  lw.chart.remove.mockClear();
  lw.series.setData.mockClear();
  lw.series.setMarkers.mockClear();
  h.use.mockReset();
});

describe("PriceSparkline — honest loudness", () => {
  it("a no-forward-bar episode draws NO chart, a quiet line, and never fetches", () => {
    h.use.mockReturnValue({ data: undefined, isLoading: false, isError: false });
    render(<PriceSparkline ep={ep({ insufficient_prices: true })} asof="2026-07-15" />);
    expect(screen.getByText("no price path yet")).toBeInTheDocument();
    expect(lw.createChart).not.toHaveBeenCalled();
    // the predicate disabled the query (enabled=false) — no wasted fetch for a path we won't draw
    expect(h.use).toHaveBeenCalledWith(expect.objectContaining({ armDate: "2026-06-01" }), false);
  });

  it("a single-bar (thin) series draws NO chart, just the quiet line", () => {
    h.use.mockReturnValue({ data: { bars: [bar("2026-06-01", 100)] }, isLoading: false, isError: false });
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

describe("PriceSparkline — the drawn path", () => {
  const DATA = {
    data: { bars: [bar("2026-06-01", 100), bar("2026-06-08", 104), bar("2026-06-15", 102)] },
    isLoading: false,
    isError: false,
  };

  it("draws a close LINE from the bars and enables the query (drawer open)", () => {
    h.use.mockReturnValue(DATA);
    render(<PriceSparkline ep={ep({ peak_date: "2026-06-08" })} asof="2026-07-15" />);
    expect(h.use).toHaveBeenCalledWith(expect.objectContaining({ armDate: "2026-06-01" }), true);
    expect(lw.createChart).toHaveBeenCalledTimes(1);
    expect(lw.series.setData).toHaveBeenCalledWith([
      { time: "2026-06-01", value: 100 },
      { time: "2026-06-08", value: 104 },
      { time: "2026-06-15", value: 102 },
    ]);
  });

  it("marks arm / peak / last (ascending time), the last labeled 'exit' when matured", () => {
    h.use.mockReturnValue(DATA);
    render(<PriceSparkline ep={ep({ peak_date: "2026-06-08", truncated: false })} asof="2026-07-15" />);
    const markers = lw.series.setMarkers.mock.calls[0][0];
    expect(markers.map((m: { text: string }) => m.text)).toEqual(["arm", "peak", "exit"]);
    expect(markers.map((m: { time: string }) => m.time)).toEqual([
      "2026-06-01",
      "2026-06-08",
      "2026-06-15",
    ]);
  });

  it("labels the last bar 'last bar' (not 'exit') on a truncated episode", () => {
    h.use.mockReturnValue(DATA);
    render(<PriceSparkline ep={ep({ truncated: true })} asof="2026-07-15" />);
    const markers = lw.series.setMarkers.mock.calls[0][0];
    expect(markers.map((m: { text: string }) => m.text)).toContain("last bar");
    expect(markers.map((m: { text: string }) => m.text)).not.toContain("exit");
  });

  it("disposes the chart on unmount (no leak)", () => {
    h.use.mockReturnValue(DATA);
    const { unmount } = render(<PriceSparkline ep={ep()} asof="2026-07-15" />);
    expect(lw.chart.remove).not.toHaveBeenCalled();
    unmount();
    expect(lw.chart.remove).toHaveBeenCalledTimes(1);
  });
});
