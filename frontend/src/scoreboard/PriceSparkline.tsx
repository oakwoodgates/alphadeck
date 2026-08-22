import { useEffect, useRef, useState } from "react";
import { ColorType, createChart, type ISeriesApi, type Time } from "lightweight-charts";

import type { PriceBar, ScoreboardEpisodeOut } from "../api/hooks";
import {
  defaultVisibleRange,
  episodeMarkers,
  familyCls,
  insiderSetAside,
  legendEntries,
  type OverlayEvent,
  overlayTooltip,
  type PriceMarkerKind,
  stackChips,
  volumeData,
} from "./overlay";

// The drawer's episode chart (Slice 3, evolved in Slice A/R1, and — Slice B — lifted onto shared props). It
// draws the CLOSE line with faint SMA 50/200 context behind it, plus a CUSTOM numbered-chip overlay for the
// RECORDED event families (insider buy / arm trigger / lifecycle / operator — A2). Slice A2 also adds the
// un-numbered OUTCOME markers (entry/exit/peak — derived points, not recorded events → setMarkers on the
// close series, never chips) and an expanded-mode-only volume histogram on its own overlay price scale
// ("vol" — the #229 close pin never sees it). The DEFAULT visible range is the
// recent episode; the user pans/zooms to reach earlier dots (zoom de-crowds a dense cluster). Each chip is
// hoverable (a tooltip with the disclosure lag + market-price context, and a guide-line to its point on the
// price), a legend names the present families, and collision-stacking never drops a chip (a hidden one shows
// as "+N"). #229 lessons preserved: the close series pins the y-axis autoscale (SMA lines + chips never
// re-inflate it), the chart draws on ≥2 bars (a just-armed episode still shows its pre-arm path — only
// genuinely-thin/absent data falls back to the quiet line), and the chart disposes on unmount.
// lightweight-charts' built-in setMarkers is static + hover-less, so the chips are a positioned DOM layer.
//
// Slice B: the price WINDOW fetch and the ONE `buildOverlayEvents` numbering now live in the parent
// (EpisodeScorecard), which passes `bars` + `events` down so the chart and the ledger share the exact same
// numbered array (row #N ↔ chip #N, built once). This component owns only the imperative chart, the chip
// positioning, and the lightweight cross-highlight — the `active` chip ring driven by `activeN`. ANTI-STORM
// INVARIANT: `activeN` is NOT a chart-effect dependency — an active change re-runs only the cheap chip-class
// render (a map over ~N buttons), NEVER the canvas rebuild.

// Canvas can't read CSS vars → the LINE colors are hard-coded (the chip DOM reads vars, so it stays
// theme-aware). SMA lines sit BEHIND the close, fainter the longer the window.
const CLOSE = "#9aa3b0"; // --txt-2 (the #229 line, full weight)
const SMA50 = "#586374"; // --incub (faint)
const SMA200 = "#3c4450"; // --txt-4 (fainter)
const AXIS = "#5a6470"; // --txt-3
const GRID = "#1e232c"; // --line
// A2: the un-numbered OUTCOME markers (entry/exit/peak) — muted AXIS-grade grays (#7: derived outcome
// points annotate the path, they never compete with the recorded-event chips), live-eye tunable.
const MARKER_COLOR: Record<PriceMarkerKind, string> = {
  entry: "#5a6470",
  exit: "#5a6470",
  peak: "#586374",
};
// A2: the expanded-mode volume histogram — very faint (between GRID and --txt-4), tape texture only.
const VOLUME = "#232a35";

// Layout constants for the chip overlay (positioning is live-verified by eye; the math is pure/tested).
const INLINE_H = 132;
const EXPAND_H = 360;
const EXPAND_WIDTH_THRESHOLD = 720; // drawer default ≈ 600px → chart ≈ 560px; expanded ≈ 100vw
const CHIP_W = 20; // min horizontal separation before two chips stack
const CHIP_H = 15; // chip height (for the guide-line origin)
const CHIP_HALF = 11; // half chip width (for the edge clamp)
const CHIP_STEP = 18; // vertical stack pitch
const TOP_PAD = 6; // the chip band starts here, below the top edge

interface Chip {
  event: OverlayEvent;
  x: number;
  y: number;
}
interface OverflowBadge {
  x: number;
  ns: number[];
}
interface Hover {
  event: OverlayEvent;
  x: number;
  y: number;
  priceY: number | null; // the y of the close on the event's date — the guide-line's foot
}

export function PriceSparkline({
  ep,
  bars,
  events,
  activeN,
  onActivate,
  loading = false,
  error = false,
}: {
  ep: ScoreboardEpisodeOut;
  /** The whole loaded price window [floor, end], fetched by the parent (EpisodeScorecard). */
  bars: PriceBar[];
  /** The unified numbered events, built ONCE in the parent so this chart and the ledger share the array. */
  events: OverlayEvent[];
  /** The currently cross-highlighted event number (from a ledger-row hover), or null. */
  activeN: number | null;
  /** Report a chip hover up so the paired ledger row highlights (and clear on leave). */
  onActivate: (n: number | null) => void;
  loading?: boolean;
  error?: boolean;
}) {
  const hasPath = bars.length >= 2;

  const boxRef = useRef<HTMLDivElement>(null);
  const seriesRef = useRef<ISeriesApi<"Line"> | null>(null); // for priceToCoordinate on hover (guide-line)
  const plotWRef = useRef(320); // the plot width, for clamping chips + the tooltip inside the chart
  const [chips, setChips] = useState<Chip[]>([]);
  const [overflow, setOverflow] = useState<OverflowBadge[]>([]);
  const [hover, setHover] = useState<Hover | null>(null);
  // Legend reflects every family PRESENT in the loaded universe (not just the placed chips): a family
  // entirely off the visible range or in the "+N" overflow must still name itself (honest loudness #7).
  const legend = legendEntries(events);

  useEffect(() => {
    const el = boxRef.current;
    const data = bars;
    if (!el || data.length < 2) return; // nothing to draw — the quiet line renders instead (below)
    setHover(null);
    const width0 = el.clientWidth || 320;
    const height0 = width0 > EXPAND_WIDTH_THRESHOLD ? EXPAND_H : INLINE_H;

    const chart = createChart(el, {
      width: width0,
      height: height0,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: AXIS,
        fontSize: 10,
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
      },
      grid: { vertLines: { visible: false }, horzLines: { color: GRID, style: 1 } },
      rightPriceScale: { borderVisible: false },
      leftPriceScale: { visible: false },
      // R4: relax the fixed edges so the user can pan past the default episode view to the earlier dots.
      timeScale: { borderVisible: false, fixLeftEdge: false, fixRightEdge: false },
      crosshair: { horzLine: { visible: false }, vertLine: { visible: false } },
      handleScroll: true, // R4: pan/zoom reveals the loaded universe left of the default view
      handleScale: true,
    });

    // A2: the volume histogram — EXPANDED MODE ONLY (the inline 132px chart has no room for a second
    // register; "expanded" here IS the measured width crossing EXPAND_WIDTH_THRESHOLD, the same seam
    // that switches the chart height — no prop from the Drawer). Created first so it draws BENEATH the
    // SMA + close lines, on its OWN overlay price scale ("vol"): the right price scale — where the #229
    // close pin lives — never sees it, so the autoscale pin is structurally undisturbed. The scale
    // margins confine the bars to the bottom ~22%. Null-volume bars are skipped upstream (volumeData).
    const vol = chart.addHistogramSeries({
      color: VOLUME,
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
      visible: width0 > EXPAND_WIDTH_THRESHOLD,
    });
    chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
    vol.setData(volumeData(data).map((p) => ({ time: p.time as Time, value: p.value })));

    // SMA context, drawn BEHIND the close. Each SMA series is EXCLUDED from the autoscale
    // (`autoscaleInfoProvider: () => null`) so it can't re-inflate the y-axis — the #229 pin below stays
    // authoritative. Null SMA points (the honest left-edge gap) are filtered out: the line simply begins
    // where enough history exists, never back-padded.
    const addSma = (color: string, pick: (b: (typeof data)[number]) => number | null | undefined) => {
      const s = chart.addLineSeries({
        color,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
        autoscaleInfoProvider: () => null,
      });
      s.setData(
        data
          .filter((b) => pick(b) != null)
          .map((b) => ({ time: b.d as Time, value: pick(b) as number })),
      );
    };
    addSma(SMA200, (b) => b.sma200);
    addSma(SMA50, (b) => b.sma50);

    // Pin the price scale to the actual close range over the WHOLE loaded universe (the #229 fix): the SMA
    // and chip space never fold into the autoscale, and the pin holds through pan/zoom (a fixed y over all
    // loaded closes → panning to earlier bars keeps the line in view).
    const closes = data.map((b) => b.close);
    const lo = Math.min(...closes);
    const hi = Math.max(...closes);
    const pad = (hi - lo) * 0.15 || 1; // a flat series still gets a sane band, never a zero range
    const series: ISeriesApi<"Line"> = chart.addLineSeries({
      color: CLOSE,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: false,
      autoscaleInfoProvider: () => ({ priceRange: { minValue: lo - pad, maxValue: hi + pad } }),
    });
    series.setData(data.map((b) => ({ time: b.d as Time, value: b.close })));
    seriesRef.current = series;

    // A2: the un-numbered OUTCOME markers (entry/exit/peak) ride the pinned close series. The
    // marker-autoscale trap does not bite here: in v4 marker margins enter the autoscale only through
    // the series' DEFAULT computation, and the close series' `autoscaleInfoProvider` above REPLACES
    // that wholesale (it ignores the `original` callback) — so markers can never re-inflate the
    // y-axis, and at close prices they sit inside the pinned band. Selection is pure (episodeMarkers,
    // tested); only this mapping to the lib's shape lives on the canvas side.
    series.setMarkers(
      episodeMarkers(ep, data).map((m) => ({
        time: m.time as Time,
        position: m.position,
        shape: m.shape,
        text: m.text,
        color: MARKER_COLOR[m.kind],
        size: 1,
      })),
    );

    // R2: DEFAULT the visible range to the recent episode (not fitContent-to-all) — earlier dots stay
    // loaded, left of view, reached by panning.
    const vis = defaultVisibleRange(data, ep.arm_date);
    if (vis) chart.timeScale().setVisibleRange({ from: vis.from as Time, to: vis.to as Time });
    else chart.timeScale().fitContent();

    // The nearest bar to a date (either side) → a real x on the business-day scale; timeToCoordinate
    // returns null when that bar is OUTSIDE the visible range (R5: a chip fully off-view is hidden).
    const coordFor = (isoDate: string): number | null => {
      let best = data[0].d;
      let bestDiff = Infinity;
      const t = Date.parse(isoDate);
      for (const b of data) {
        const diff = Math.abs(Date.parse(b.d) - t);
        if (diff < bestDiff) {
          bestDiff = diff;
          best = b.d;
        }
      }
      const x = chart.timeScale().timeToCoordinate(best as Time);
      return x == null ? null : x;
    };

    const reposition = () => {
      const w = el.clientWidth || 320;
      plotWRef.current = w;
      const height = w > EXPAND_WIDTH_THRESHOLD ? EXPAND_H : INLINE_H;
      const positioned = events
        .map((e) => ({ e, x: coordFor(e.date) }))
        .filter((p): p is { e: OverlayEvent; x: number } => p.x != null); // R5: off-view chips are hidden
      const maxLevels = Math.max(2, Math.floor((height * 0.55) / CHIP_STEP));
      const { placed, overflow: spill } = stackChips(
        positioned.map((p) => ({ n: p.e.n, x: Math.round(p.x), family: p.e.family })),
        { chipW: CHIP_W, maxLevels },
      );
      const byN = new Map(positioned.map((p) => [p.e.n, p.e]));
      // R5: clamp x so the whole chip stays on-screen at the edges (as the tooltip does).
      const clampX = (x: number) => Math.min(Math.max(x, CHIP_HALF), w - CHIP_HALF);
      setChips(
        placed.map((pl) => ({ event: byN.get(pl.n)!, x: clampX(pl.x), y: TOP_PAD + pl.level * CHIP_STEP })),
      );
      // cluster spilled chips into visible "+N" badges (never a silent drop — #9)
      const clustered: OverflowBadge[] = [];
      for (const it of [...spill].sort((a, b) => a.x - b.x)) {
        const cx = clampX(it.x);
        const lastB = clustered[clustered.length - 1];
        if (lastB && cx - lastB.x < CHIP_W * 1.5) lastB.ns.push(it.n);
        else clustered.push({ x: cx, ns: [it.n] });
      }
      setOverflow(clustered);
    };
    reposition();
    // R4: the chip layer tracks pan/zoom — reposition on every visible-range change.
    chart.timeScale().subscribeVisibleTimeRangeChange(reposition);

    // Responsive to the drawer's default (600px) vs expanded (100vw) width — CANVAS SIZE only (the data
    // window is fixed). jsdom lacks ResizeObserver — guard so the component (and its tests) never depend on
    // it. Crossing the threshold grows the chart height; the visible range (and any user pan) is preserved.
    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            const w = el.clientWidth || 320;
            chart.applyOptions({ width: w, height: w > EXPAND_WIDTH_THRESHOLD ? EXPAND_H : INLINE_H });
            // A2: the volume flips with the SAME width seam — a visibility toggle only, never an
            // effect re-run (the anti-storm invariant: the canvas is not rebuilt on expand/collapse).
            vol.applyOptions({ visible: w > EXPAND_WIDTH_THRESHOLD });
            reposition();
          })
        : null;
    ro?.observe(el);

    return () => {
      ro?.disconnect();
      chart.timeScale().unsubscribeVisibleTimeRangeChange(reposition);
      chart.remove(); // dispose — lightweight-charts leaks its canvas otherwise
      seriesRef.current = null;
      setChips([]);
      setOverflow([]);
    };
    // `bars` + `events` are referentially stable per fetch (parent memoizes them off react-query's stable
    // q.data). ep is stable per drawer-open. `activeN` is DELIBERATELY absent — it drives only the chip-class
    // render below, never a canvas rebuild (the anti-storm invariant).
  }, [bars, events, ep]);

  if (error) return <div className="sc-spark sc-spark-empty">price path unavailable</div>;
  if (!hasPath)
    return (
      <div className="sc-spark sc-spark-empty">
        {loading ? "reading the price path…" : "no price path yet"}
      </div>
    );

  const tip = hover ? overlayTooltip(hover.event) : null;
  const tipX = hover ? Math.min(Math.max(hover.x, 76), plotWRef.current - 76) : 0;
  const onEnter = (c: Chip) => {
    setHover({
      event: c.event,
      x: c.x,
      y: c.y,
      // the guide-line foot: the close on the event's date, mapped to a y on the price line
      priceY:
        c.event.closeThatDay != null
          ? (seriesRef.current?.priceToCoordinate(c.event.closeThatDay) ?? null)
          : null,
    });
    onActivate(c.event.n); // cross-highlight: light up the paired ledger row
  };
  const onLeave = () => {
    setHover(null);
    onActivate(null);
  };
  return (
    <div className="sc-spark">
      <div className="sc-spark-plot">
        <div ref={boxRef} className="sc-spark-chart" aria-label="episode price path" />
        <div className="ov-layer">
          {/* R3: a thin guide-line from the hovered chip to its point on the price line */}
          {hover && hover.priceY != null && (
            <div
              className="ov-guide"
              style={{
                left: hover.x,
                top: Math.min(hover.y + CHIP_H, hover.priceY),
                height: Math.abs(hover.priceY - (hover.y + CHIP_H)),
              }}
            />
          )}
          {chips.map((c) => (
            <button
              key={c.event.n}
              type="button"
              // S2c: a set-aside buy (primary-market / implausible) renders GREYED, never hidden (WB #2)
              className={`ov-chip ${familyCls(c.event.family)}${
                c.event.family === "insider" && insiderSetAside(c.event.buy) ? " ov-setaside" : ""
              }${c.event.n === activeN ? " active" : ""}`}
              style={{ left: c.x, top: c.y }}
              onMouseEnter={() => onEnter(c)}
              onMouseLeave={onLeave}
              onFocus={() => onEnter(c)}
              onBlur={onLeave}
              aria-label={`event ${c.event.n}: ${overlayTooltip(c.event).title}`}
            >
              {c.event.n}
            </button>
          ))}
          {overflow.map((o, i) => (
            <span
              key={`more-${i}`}
              className="ov-more"
              style={{ left: o.x, top: TOP_PAD }}
              title={`${o.ns.length} more: #${o.ns.join(", #")}`}
            >
              +{o.ns.length}
            </span>
          ))}
        </div>
        {tip && (
          <div className="ov-tip" style={{ left: tipX, top: hover!.y + 20 }} role="tooltip">
            <div className="ov-tip-title">
              <span className="ov-tip-n">#{hover!.event.n}</span> {tip.title}
            </div>
            {tip.lines.map((l, i) => (
              <div key={i} className="ov-tip-line">
                {l}
              </div>
            ))}
          </div>
        )}
      </div>
      {legend.length > 0 && (
        <div className="ov-legend">
          {legend.map((l) => (
            <span key={l.family} className="ov-legend-item">
              <span className={`ov-dot ${l.cls}`} />
              {l.label}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
