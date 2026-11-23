import { useEffect, useMemo, useRef, useState } from "react";
import { ColorType, LineStyle, createChart, type IChartApi, type Time } from "lightweight-charts";

import { STATE_CLASS, STATE_LABEL } from "../../util/format";
import type { DashboardProps } from "../registry";
import "./narrative-index.css";
import {
  DEFAULT_SORT,
  SORT_OPTIONS,
  WINDOW_OPTIONS,
  createdIndex,
  fmtDay,
  fmtLevelPct,
  fmtPct,
  linePath,
  nextSort,
  sharedYDomain,
  sinceWords,
  sortCards,
  windowLabel,
  xAt,
  yAt,
  yDomain,
  type Domain,
  type SortKey,
  type SortState,
} from "./indexView";
import {
  useNarrativeCard,
  useNarrativeIndex,
  type IndexWindow,
  type NarrativeBenchmarkOut,
  type NarrativeCardOut,
  type NarrativeIndexOut,
} from "./useNarrativeIndex";
import { RecordStartNote } from "../RecordStartNote";

// Narrative Index (docs/dashboards/narrative-index.md): each card's basket as of the as-of as one equal-weighted
// line against SPY and IWM, with the day the card was created marked. Display only. Every number (the index,
// the returns, who counts, the flat sessions) is computed server-side; this page picks the window, orders the
// cards and draws them. The minis are inline SVG (never one chart-library instance per card); the enlarged
// view is SVG too, off the same points.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";

const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

function cacheWords(d: NarrativeIndexOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

function benchWords(bench: readonly NarrativeBenchmarkOut[]): string {
  const absent = bench.filter((b) => !b.available);
  if (absent.length === 0) return `${bench.map((b) => b.symbol).join(" and ")} over the same sessions`;
  return absent.map((b) => `${b.symbol} line absent: ${b.reason ?? "no tape"}`).join(" · ");
}

/** The quiet line that shows the work, every number off the payload. */
function workLine(d: NarrativeIndexOut): string {
  const cards =
    d.cards_not_yet_created > 0
      ? `${plural(d.cards_counted, "live card", "live cards")} counted (${d.cards_not_yet_created} created after the as-of left out)`
      : `${plural(d.cards_counted, "live card", "live cards")} counted`;
  const nominal = d.window_sessions[d.window] ?? d.sessions;
  const span =
    d.dates.length > 0 ? `, ${fmtDay(d.dates[0])} → ${fmtDay(d.dates[d.dates.length - 1])}` : "";
  const short = d.sessions < nominal ? " (the tape is shorter than the window)" : "";
  return [
    cards,
    `${windowLabel(d.window)} window: ${plural(d.sessions, "session", "sessions")}${span}${short}`,
    d.include_silent
      ? "Context and Bench included"
      : `Context and Bench left out (${plural(d.silent_rows_left_out, "basket row", "basket rows")})`,
    "equal weight, rebalanced daily, from 100",
    d.flagged_companies > 0
      ? `names with a flagged price history are left out (${plural(d.flagged_companies, "company", "companies")})`
      : "names with a flagged price history are left out",
    `${plural(d.companies, "company", "companies")}, ${d.priced_companies.toLocaleString("en-US")} priced in the 1Y`,
    `${plural(d.unresolved_rows, "basket row", "basket rows")} without a resolved security (counted, never priced)`,
    benchWords(d.benchmarks),
    d.session_source === "companies" ? "sessions from the companies' bars (no benchmark tape)" : null,
    cacheWords(d),
    `as of ${fmtDay(d.asof)}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function StateChip({ state }: { state: NarrativeCardOut["state"] }) {
  if (!state) return <span className="ni-st none">no recorded call</span>;
  return <span className={`ni-st ${STATE_CLASS[state] ?? ""}`}>{STATE_LABEL[state] ?? state}</span>;
}

const numTone = (v: number | null | undefined) => (v == null || v === 0 ? "" : v > 0 ? " pos" : " neg");

/** The numbers above a chart. */
function Numbers({ c, window }: { c: NarrativeCardOut; window: IndexWindow }) {
  return (
    <div className="ni-nums">
      <span className={`ni-val${numTone(c.ret)}`} title="the index return over the window">
        {windowLabel(window)} {fmtPct(c.ret)}
      </span>
      <span className={`ni-val${numTone(c.vs_spy)}`} title="the window return minus SPY's over the same sessions">
        vs SPY {fmtPct(c.vs_spy)}
      </span>
      <span
        className={c.created_in_window ? `ni-val${numTone(c.since_created)}` : undefined}
        title={
          c.created_in_window
            ? `from the ${fmtDay(c.created_base)} close (created ${fmtDay(c.created)})`
            : c.created
              ? `created ${fmtDay(c.created)}, before the window's first session`
              : "creation day unknown"
        }
      >
        {sinceWords(c)}
      </span>
      <span title="names with at least one daily return in the window / names in the basket">
        {c.priced}/{c.names} priced
      </span>
      {c.sessions_flat > 0 && (
        <span title="window sessions no name could contribute to: the index carried flat">
          {plural(c.sessions_flat, "session", "sessions")} flat
        </span>
      )}
    </div>
  );
}

/** The card's quiet note on the names left out of its line (a flagged price history); nothing when none is. */
function FlaggedNote({ c }: { c: NarrativeCardOut }) {
  if (c.flagged.length === 0) return null;
  return (
    <div
      className="ni-note"
      title="their data flags reach their price readings (the Cockpit grays those cells), so they never contribute a daily return"
    >
      {plural(c.flagged.length, "name", "names")} left out:{" "}
      {c.flagged.map((f) => `${f.ticker}, ${f.reason}`).join(" · ")}
    </div>
  );
}

function benchLine(bench: readonly NarrativeBenchmarkOut[], symbol: string): number[] | null {
  const b = bench.find((x) => x.symbol === symbol);
  return b?.available && b.points ? b.points : null;
}

const MINI_W = 200;
const MINI_H = 56;

function MiniChart({
  c,
  dates,
  bench,
  sharedDomain,
}: {
  c: NarrativeCardOut;
  dates: readonly string[];
  bench: readonly NarrativeBenchmarkOut[];
  sharedDomain: Domain | null;
}) {
  const spy = benchLine(bench, "SPY");
  const iwm = benchLine(bench, "IWM");
  const domain = sharedDomain ?? yDomain([c.points, spy, iwm]);
  const at = createdIndex(dates, c.created_base);
  const base = yAt(100, domain, MINI_H);
  const lastVal = c.points.length > 0 ? c.points[c.points.length - 1] : null;
  const lineTone = numTone(c.ret);
  return (
    <div className="ni-mini-wrap">
      <svg
        className="ni-mini"
        viewBox={`0 0 ${MINI_W} ${MINI_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${c.thesis_name} index ${fmtPct(c.ret)}`}
      >
        <line className="ni-base" x1={0} x2={MINI_W} y1={base} y2={base} />
        {spy && <path className="ni-bench spy" d={linePath(spy, domain, MINI_W, MINI_H)} />}
        {iwm && <path className="ni-bench iwm" d={linePath(iwm, domain, MINI_W, MINI_H)} />}
        {at != null && (
          <line
            className="ni-created"
            data-testid="ni-created"
            x1={xAt(at, dates.length, MINI_W)}
            x2={xAt(at, dates.length, MINI_W)}
            y1={0}
            y2={MINI_H}
          >
            <title>created {fmtDay(c.created)}</title>
          </line>
        )}
        {c.points.length > 1 && <path className={`ni-line${lineTone}`} d={linePath(c.points, domain, MINI_W, MINI_H)} />}
      </svg>
      <div className="ni-mini-scale" aria-hidden="true">
        <span>{fmtLevelPct(domain[1])}</span>
        <span>{lastVal != null ? `now ${fmtLevelPct(lastVal)}` : ""}</span>
        <span>{fmtLevelPct(domain[0])}</span>
      </div>
    </div>
  );
}

// the enlarged chart: interactive lightweight-charts canvas (pan, zoom, crosshair, contributors overlay)
const NI_LWC_H = 240;
const NI_AXIS = "#5a6470"; // --txt-3
const NI_GRID = "#1e232c"; // --line
const NI_BASE = "#3c4450"; // --txt-4
const NI_POS = "#5fa87b"; // muted --pos
const NI_NEG = "#b56565"; // muted --neg
const NI_FLAT = "#d8dee9"; // --txt
const NI_SPY = "#7892b5"; // muted --leader
const NI_IWM = "#8a7ba6"; // muted --activist
const NI_CONTRIB = "#252c38"; // faint histogram texture

function BigChart({
  c,
  dates,
  bench,
  contributors,
}: {
  c: NarrativeCardOut;
  dates: readonly string[];
  bench: readonly NarrativeBenchmarkOut[];
  contributors: readonly number[] | null;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const spy = benchLine(bench, "SPY");
  const iwm = benchLine(bench, "IWM");
  const n = dates.length;
  const at = createdIndex(dates, c.created_base);
  const i = hover ?? Math.max(0, n - 1);
  const maxNames = Math.max(1, c.names, ...(contributors ?? [0]));

  const idxVal = c.points[i];
  const spyVal = spy?.[i];
  const iwmVal = iwm?.[i];
  const vsSpyVal = idxVal != null && spyVal != null ? idxVal - spyVal : null;
  const lineTone = numTone(c.ret);

  useEffect(() => {
    const el = boxRef.current;
    if (!el || dates.length < 2) return;
    setHover(null);

    const width0 = el.clientWidth || 720;
    const chart = createChart(el, {
      width: width0,
      height: NI_LWC_H,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: NI_AXIS,
        fontSize: 10,
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
      },
      grid: { vertLines: { visible: false }, horzLines: { color: NI_GRID, style: LineStyle.Dotted } },
      rightPriceScale: { borderVisible: false },
      leftPriceScale: { visible: false },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: {
        vertLine: { color: NI_AXIS, width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#171b22" },
        horzLine: { color: NI_BASE, width: 1, style: LineStyle.Dotted, labelBackgroundColor: "#171b22" },
      },
      handleScroll: true,
      handleScale: true,
    });
    chartRef.current = chart;

    if (contributors && contributors.length === dates.length) {
      const hist = chart.addHistogramSeries({
        color: NI_CONTRIB,
        priceScaleId: "contrib",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      chart.priceScale("contrib").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      hist.setData(dates.map((dt, j) => ({ time: dt as Time, value: contributors[j] ?? 0 })));
    }

    if (iwm) {
      const iwmSeries = chart.addLineSeries({
        color: NI_IWM,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerRadius: 3,
      });
      iwmSeries.setData(
        dates
          .map((dt, j) => (iwm[j] != null ? { time: dt as Time, value: iwm[j] as number } : null))
          .filter((p): p is { time: Time; value: number } => p !== null),
      );
    }

    if (spy) {
      const spySeries = chart.addLineSeries({
        color: NI_SPY,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerRadius: 3,
      });
      spySeries.setData(
        dates
          .map((dt, j) => (spy[j] != null ? { time: dt as Time, value: spy[j] as number } : null))
          .filter((p): p is { time: Time; value: number } => p !== null),
      );
    }

    const idxColor = lineTone === " pos" ? NI_POS : lineTone === " neg" ? NI_NEG : NI_FLAT;
    const idxSeries = chart.addLineSeries({
      color: idxColor,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 4,
    });
    idxSeries.setData(
      dates
        .map((dt, j) => (c.points[j] != null ? { time: dt as Time, value: c.points[j] as number } : null))
        .filter((p): p is { time: Time; value: number } => p !== null),
    );
    idxSeries.createPriceLine({
      price: 100,
      color: NI_BASE,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "100",
    });

    if (at != null && dates[at]) {
      idxSeries.setMarkers([
        {
          time: dates[at] as Time,
          position: "belowBar",
          shape: "arrowUp",
          color: NI_AXIS,
          text: `created ${fmtDay(c.created)}`,
          size: 1,
        },
      ]);
    }

    chart.timeScale().fitContent();

    const dateToIdx = new Map(dates.map((dt, j) => [dt, j]));
    const onCrosshair = (param: { time?: Time }) => {
      if (!param.time) {
        setHover(null);
        return;
      }
      const key = typeof param.time === "string" ? param.time : null;
      setHover(key != null ? (dateToIdx.get(key) ?? null) : null);
    };
    chart.subscribeCrosshairMove(onCrosshair);

    const ro =
      typeof ResizeObserver !== "undefined"
        ? new ResizeObserver(() => {
            const w = el.clientWidth;
            if (w > 0) chart.applyOptions({ width: w });
          })
        : null;
    ro?.observe(el);

    return () => {
      ro?.disconnect();
      chart.unsubscribeCrosshairMove(onCrosshair);
      chart.remove();
      chartRef.current = null;
    };
  }, [c, dates, spy, iwm, contributors, at, lineTone]);

  return (
    <div className="ni-big-chart">
      <div className="ni-readout-row">
        <div className="ni-readout" data-testid="ni-big-readout">
          {n > 0 ? (
            <>
              <span className="ni-ro-date">{fmtDay(dates[i])}</span>
              <span className="ni-key idx">
                index {idxVal?.toFixed(1) ?? "—"} ({fmtLevelPct(idxVal)})
              </span>
              {spy && (
                <span className="ni-key spy">
                  SPY {spyVal?.toFixed(1) ?? "—"} ({fmtLevelPct(spyVal)})
                </span>
              )}
              {vsSpyVal != null && (
                <span className={`ni-val${numTone(vsSpyVal)}`}>
                  spread {vsSpyVal > 0 ? "+" : ""}
                  {vsSpyVal.toFixed(1)}pp
                </span>
              )}
              {iwm && (
                <span className="ni-key iwm">
                  IWM {iwmVal?.toFixed(1) ?? "—"} ({fmtLevelPct(iwmVal)})
                </span>
              )}
              {contributors && (
                <span>
                  {contributors[i] ?? 0} of {c.names} names contributing
                </span>
              )}
              {at != null && (
                <span className="ni-key created" data-testid="ni-created-big">
                  created {fmtDay(c.created)}
                </span>
              )}
            </>
          ) : (
            <span>no sessions in this window</span>
          )}
        </div>
        {n >= 2 && (
          <button
            type="button"
            className="ni-reset-zoom"
            title="Reset pan and zoom to full window"
            onClick={() => chartRef.current?.timeScale().fitContent()}
          >
            Reset zoom
          </button>
        )}
      </div>
      <div
        ref={boxRef}
        className="ni-lwc-box"
        role="img"
        aria-label={`${c.thesis_name} enlarged`}
        data-testid="ni-big-canvas"
      />
      {contributors && contributors.length === n && n > 0 && (
        <div className="ni-contrib-note" role="img" aria-label="names contributing each session">
          bottom bars: names contributing each session (up to {maxNames}) · scroll to zoom, drag to pan
        </div>
      )}
    </div>
  );
}

function Enlarged({
  c,
  data,
  includeSilent,
  onClose,
  onSelect,
}: {
  c: NarrativeCardOut;
  data: NarrativeIndexOut;
  includeSilent: boolean;
  onClose: () => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const q = useNarrativeCard(data.asof, data.window, includeSilent, c.thesis_id);
  const detail = q.data?.window === data.window ? q.data : undefined;
  return (
    <section className="ni-tile big" aria-label={`${c.thesis_name}, enlarged`}>
      <div className="ni-head">
        <button
          type="button"
          className="ni-name"
          title={`Open ${c.thesis_name} in the Cockpit`}
          onClick={() => onSelect(c.thesis_id)}
        >
          {c.thesis_name}
        </button>
        <StateChip state={c.state} />
        <button type="button" className="ni-close" onClick={onClose} aria-label={`Close ${c.thesis_name}`}>
          Close ✕
        </button>
      </div>
      <Numbers c={c} window={data.window} />
      <FlaggedNote c={c} />
      <div className="ni-legend">
        <span className="ni-key idx">— index</span>
        <span className="ni-key spy">-- SPY</span>
        <span className="ni-key iwm">·· IWM</span>
        <span className="ni-key created">│ created</span>
        <span>the bars below: names contributing each session (of {c.names})</span>
      </div>
      <BigChart c={c} dates={data.dates} bench={data.benchmarks} contributors={detail?.contributors ?? null} />
      {q.isLoading && <div className="dash-note">Loading the names…</div>}
      {q.error && <div className="dash-note">Could not load this card's names.</div>}
      {detail && (
        <table className="ni-members">
          <thead>
            <tr>
              <th>Name</th>
              <th className="ni-num">Sessions</th>
              <th>First</th>
              <th>Last</th>
            </tr>
          </thead>
          <tbody>
            {detail.members.map((m) => (
              <tr key={m.security_id ?? `u-${m.ticker}`} className={m.sessions === 0 ? "quiet" : undefined}>
                <td className="ni-tk">
                  {m.security_id ? (
                    <button
                      type="button"
                      className="ni-tk-btn"
                      title={`Open ${c.thesis_name} on ${m.ticker}`}
                      onClick={() => onSelect(c.thesis_id, m.ticker)}
                    >
                      {m.ticker}
                    </button>
                  ) : (
                    m.ticker
                  )}
                </td>
                <td className="ni-num">
                  {m.security_id == null
                    ? "no resolved security"
                    : m.flag != null
                      ? `left out: ${m.flag}`
                      : `${m.sessions}/${detail.sessions}`}
                </td>
                <td>{m.first ? fmtDay(m.first) : "—"}</td>
                <td>{m.last ? fmtDay(m.last) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function Tile({
  c,
  data,
  sharedDomain,
  onOpen,
  onSelect,
}: {
  c: NarrativeCardOut;
  data: NarrativeIndexOut;
  sharedDomain: Domain | null;
  onOpen: () => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  return (
    <section className="ni-tile" aria-label={c.thesis_name}>
      <div className="ni-head">
        <button
          type="button"
          className="ni-name"
          title={`Open ${c.thesis_name} in the Cockpit`}
          onClick={() => onSelect(c.thesis_id)}
        >
          {c.thesis_name}
        </button>
        <StateChip state={c.state} />
      </div>
      <Numbers c={c} window={data.window} />
      <FlaggedNote c={c} />
      <button type="button" className="ni-chart" onClick={onOpen} aria-label={`Enlarge ${c.thesis_name}`}>
        <MiniChart c={c} dates={data.dates} bench={data.benchmarks} sharedDomain={sharedDomain} />
      </button>
    </section>
  );
}

const STATE_FILTER_ORDER = ["armed", "warming", "managing", "incubating"] as const;

export default function NarrativeIndex({ asof, onSelectThesis }: DashboardProps) {
  const [win, setWin] = useState<IndexWindow>("1y");
  const [includeSilent, setIncludeSilent] = useState(false);
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [scaleMode, setScaleMode] = useState<"auto" | "shared">("auto");
  const [stateFilter, setStateFilter] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const q = useNarrativeIndex(asof, win, includeSilent);
  const data = q.data;

  // Escape closes an enlarged card cleanly
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const cards = useMemo(() => {
    if (!data) return [];
    const needle = query.trim().toLowerCase();
    const filtered = data.cards.filter((c) => {
      if (stateFilter != null && c.state !== stateFilter) return false;
      if (needle && !c.thesis_name.toLowerCase().includes(needle)) return false;
      return true;
    });
    return sortCards(filtered, sort);
  }, [data, sort, stateFilter, query]);

  const presentStates = useMemo(() => {
    if (!data) return [];
    const counts = new Map<string, number>();
    for (const c of data.cards) {
      if (c.state) counts.set(c.state, (counts.get(c.state) ?? 0) + 1);
    }
    return STATE_FILTER_ORDER.filter((s) => (counts.get(s) ?? 0) > 0).map((s) => ({
      value: s,
      label: STATE_LABEL[s] ?? s,
      n: counts.get(s) ?? 0,
    }));
  }, [data]);

  const sharedDomain = useMemo(() => {
    if (!data || scaleMode !== "shared") return null;
    const spy = benchLine(data.benchmarks, "SPY");
    const iwm = benchLine(data.benchmarks, "IWM");
    return sharedYDomain(cards, [spy, iwm]);
  }, [data, scaleMode, cards]);

  if (q.isLoading) {
    return (
      <div className="dash-note">
        Computing every card's index — the first load after a run or a basket edit can take a minute.
      </div>
    );
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Narrative Index.</div>;

  const onSort = (key: SortKey) => setSort((s) => nextSort(s, key));
  const spyBench = data.benchmarks.find((b) => b.symbol === "SPY" && b.available);
  const iwmBench = data.benchmarks.find((b) => b.symbol === "IWM" && b.available);

  return (
    <div className="ni">
      <RecordStartNote empty={data.cards.every((c) => c.state == null)} />
      <div className="ni-controls">
        <div className="ni-seg" role="group" aria-label="Window">
          <span className="ni-seg-label">Window</span>
          {WINDOW_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`ni-seg-btn${win === o.value ? " on" : ""}`}
              aria-pressed={win === o.value}
              onClick={() => setWin(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <div className="ni-seg" role="group" aria-label="Sort">
          <span className="ni-seg-label">Sort</span>
          {SORT_OPTIONS.map((o) => {
            const on = sort.key === o.value;
            return (
              <button
                key={o.value}
                type="button"
                className={`ni-seg-btn${on ? " on" : ""}`}
                aria-pressed={on}
                title={on ? "Pick again to reverse" : undefined}
                onClick={() => onSort(o.value)}
              >
                {o.label}
                {on && (
                  <span className="ni-arrow" aria-hidden="true">
                    {sort.dir === "asc" ? "▲" : "▼"}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <div className="ni-seg" role="group" aria-label="Y scale">
          <span className="ni-seg-label">Scale</span>
          {(
            [
              { value: "auto", label: "Auto", title: "Each mini chart fits its own high and low" },
              { value: "shared", label: "Shared", title: "Every mini chart shares one y scale across visible cards" },
            ] as const
          ).map((o) => (
            <button
              key={o.value}
              type="button"
              className={`ni-seg-btn${scaleMode === o.value ? " on" : ""}`}
              aria-pressed={scaleMode === o.value}
              title={o.title}
              onClick={() => setScaleMode(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <label className="ni-check" title="Add each card's Context and Bench names to its basket">
          <input type="checkbox" checked={includeSilent} onChange={(e) => setIncludeSilent(e.target.checked)} />
          Show Context and Bench
        </label>
        {q.isFetching && (
          <span className="dash-note">
            {data.asof !== asof || data.include_silent !== includeSilent
              ? "Computing every card's index — the first load after a run can take a minute."
              : "updating…"}
          </span>
        )}
      </div>

      <div className="ni-controls ni-subcontrols">
        {presentStates.length > 1 && (
          <div className="ni-seg" role="group" aria-label="Filter state">
            <span className="ni-seg-label">State</span>
            <button
              type="button"
              className={`ni-seg-btn${stateFilter == null ? " on" : ""}`}
              aria-pressed={stateFilter == null}
              onClick={() => setStateFilter(null)}
            >
              All
            </button>
            {presentStates.map((s) => (
              <button
                key={s.value}
                type="button"
                className={`ni-seg-btn${stateFilter === s.value ? " on" : ""}`}
                aria-pressed={stateFilter === s.value}
                onClick={() => setStateFilter((cur) => (cur === s.value ? null : s.value))}
              >
                <span className={`ni-st ${STATE_CLASS[s.value] ?? ""}`}>{s.label}</span>{" "}
                <span className="ni-chip-n">{s.n}</span>
              </button>
            ))}
          </div>
        )}
        <input
          type="search"
          className="ni-search"
          aria-label="Filter cards"
          placeholder="Filter cards…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {(spyBench || iwmBench) && (
          <div className="ni-bench-bar" data-testid="ni-bench-bar">
            {spyBench && (
              <span className="ni-key spy">
                -- SPY {windowLabel(data.window)} {fmtPct(spyBench.ret)}
              </span>
            )}
            {iwmBench && (
              <span className="ni-key iwm">
                ·· IWM {windowLabel(data.window)} {fmtPct(iwmBench.ret)}
              </span>
            )}
          </div>
        )}
      </div>

      {cards.length === 0 ? (
        <div className="dash-note">No cards at this as-of.</div>
      ) : (
        <div className="ni-grid">
          {cards.map((c) =>
            open === c.thesis_id ? (
              <Enlarged
                key={c.thesis_id}
                c={c}
                data={data}
                includeSilent={data.include_silent}
                onClose={() => setOpen(null)}
                onSelect={onSelectThesis}
              />
            ) : (
              <Tile
                key={c.thesis_id}
                c={c}
                data={data}
                sharedDomain={sharedDomain}
                onOpen={() => setOpen(c.thesis_id)}
                onSelect={onSelectThesis}
              />
            ),
          )}
        </div>
      )}

      <div className="ni-foot">
        <div className="dash-note ni-work">
          The card's basket as of {fmtDay(data.asof)}, looking back. Not the basket as it stood on each past day.
        </div>
        <div className="dash-note ni-work">{workLine(data)}</div>
        {data.roster_fallback_cards > 0 && (
          <div className="dash-note ni-work">
            Today's roster stands in for {plural(data.roster_fallback_cards, "card", "cards")}: rosters are
            point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
          </div>
        )}
        <div className="dash-note ni-legend">
          {scaleMode === "shared"
            ? "Every chart shares one y scale across visible cards; the faint flat line is 100."
            : "Each chart has its own scale (shown on the right); the faint flat line is 100."}{" "}
          Dashed blue = SPY, dotted violet = IWM; the vertical tick marks the card's creation (the last session on or
          before that day), when it falls inside the window. Click a chart to enlarge it; click a name to open the card.
        </div>
      </div>
    </div>
  );
}
