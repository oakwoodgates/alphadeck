import { useEffect, useRef } from "react";
import {
  ColorType,
  createChart,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";

import type { ScoreboardEpisodeOut } from "../api/hooks";
import { useEpisodePriceWindow } from "../api/hooks";
import { noForwardBar } from "./scorecard";

// The drawer's episode price sparkline (Slice 3) — the app's first lightweight-charts component. It draws
// a CLOSE line over the episode's asof-capped window; the wire already carries full OHLCV so a candlestick
// is a later pure-FE swap (read o/h/l from each bar), not a second contract change. Honest loudness: a
// no-forward-bar episode (<= 1 real bar) draws NO chart — a quiet "no price path yet" line, never a
// single-dot fake — and never even fetches. A running/truncated episode charts to the LAST real bar; the
// server caps at asof, so no invented line runs out to a future exit_by. The chart disposes on unmount
// (lightweight-charts leaks its canvas otherwise) and tracks the drawer's default vs expanded width.

// Palette mirrors index.css (a canvas can't read CSS vars): neutral line, green arm, amber peak, quiet last.
const LINE = "#9aa3b0"; // --txt-2
const ARM = "#46d07f"; // --pos
const PEAK = "#e0a23c"; // --warm
const LAST = "#5a6470"; // --txt-3
const AXIS = "#5a6470"; // --txt-3
const GRID = "#1e232c"; // --line

export function PriceSparkline({ ep, asof }: { ep: ScoreboardEpisodeOut; asof: string }) {
  const noBar = noForwardBar(ep);
  // A running/truncated episode has exit_by possibly in the future; the server caps at asof, so the line
  // stops at the last real bar. `end` falls back to asof when exit_by is unset (server caps there anyway).
  const end = ep.exit_by ?? asof;
  const q = useEpisodePriceWindow(
    { thesisId: ep.thesis_id, securityId: ep.security_id, armDate: ep.arm_date, end, asof },
    !noBar, // honest loudness: a no-forward-bar episode never even fetches
  );
  const bars = q.data?.bars ?? [];
  const hasPath = bars.length >= 2;

  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = boxRef.current;
    const data = q.data?.bars ?? [];
    if (!el || data.length < 2) return; // nothing to draw — the quiet line renders instead (below)

    const chart = createChart(el, {
      width: el.clientWidth || 320,
      height: 132,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: AXIS,
        fontSize: 10,
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
      },
      grid: { vertLines: { visible: false }, horzLines: { color: GRID, style: 1 } },
      rightPriceScale: { borderVisible: false },
      leftPriceScale: { visible: false },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
      handleScroll: false, // a sparkline, not an interactive chart
      handleScale: false,
    });
    // Pin the price scale to the actual close range. Otherwise lightweight-charts folds the marker
    // LABEL space into its autoscale and, on a ~132px sparkline, inflates the axis to ~2x the data
    // (measured: 179–238 for a 206–219 series) — squashing the line + peak into a narrow middle band
    // so a small move's peak reads flat/off. `data.length >= 2` holds here (the guard above).
    const closes = data.map((b) => b.close);
    const lo = Math.min(...closes);
    const hi = Math.max(...closes);
    const pad = (hi - lo) * 0.15 || 1; // a flat series still gets a sane band, never a zero range
    const series: ISeriesApi<"Line"> = chart.addLineSeries({
      color: LINE,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: lo - pad, maxValue: hi + pad } }),
    });
    series.setData(data.map((b) => ({ time: b.d as Time, value: b.close })));

    // Markers: arm (the first REAL bar, not necessarily arm_date — no bar may sit exactly on arm_date),
    // peak (only if peak_date is inside the drawn data), and the last real bar (exit / last-bar).
    const markers: SeriesMarker<Time>[] = [
      { time: data[0].d as Time, position: "belowBar", color: ARM, shape: "arrowUp", text: "arm" },
    ];
    if (ep.peak_date && data.some((b) => b.d === ep.peak_date))
      markers.push({
        time: ep.peak_date as Time,
        position: "aboveBar",
        color: PEAK,
        shape: "circle",
        text: "peak",
      });
    const last = data[data.length - 1];
    if (last.d !== data[0].d)
      markers.push({
        time: last.d as Time,
        position: "aboveBar",
        color: LAST,
        shape: "circle",
        text: ep.truncated ? "last bar" : "exit",
      });
    // lightweight-charts requires markers in ascending time order
    markers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    series.setMarkers(markers);
    chart.timeScale().fitContent();

    // Responsive to the drawer's default (600px) vs expanded (100vw) width. jsdom lacks ResizeObserver —
    // guard so the component (and its tests) never depend on it.
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth || 320 }))
        : null;
    ro?.observe(el);

    return () => {
      ro?.disconnect();
      chart.remove(); // dispose — lightweight-charts leaks its canvas otherwise
    };
    // q.data is referentially stable per fetch (react-query), so this re-runs only when the window
    // actually changes (a new episode opened in the same drawer), never every render.
  }, [q.data, ep.peak_date, ep.truncated, ep.arm_date]);

  if (noBar) return <div className="sc-spark sc-spark-empty">no price path yet</div>;
  if (q.isError) return <div className="sc-spark sc-spark-empty">price path unavailable</div>;
  if (!hasPath)
    return (
      <div className="sc-spark sc-spark-empty">
        {q.isLoading ? "reading the price path…" : "no price path yet"}
      </div>
    );
  return <div ref={boxRef} className="sc-spark sc-spark-chart" aria-label="episode price path" />;
}
