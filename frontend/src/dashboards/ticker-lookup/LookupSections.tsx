import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ColorType, LineStyle, createChart, type IChartApi, type SeriesMarker, type Time } from "lightweight-charts";

import { useAppClock, useCountdownOrigin } from "../../clock";
import { ExternalLink, isSecLink } from "../../components/ExternalLink";
import { ProvenanceSources } from "../../components/ProvenanceSources";
import { lowerFirst, reasonClause } from "../../components/valueWords";
import { PriceSparkline } from "../../scoreboard/PriceSparkline";
import type { ChartSpan, OverlayEvent } from "../../scoreboard/overlay";
import {
  businessTypeLabel,
  dayCount,
  daysFrom,
  fmtDate,
  isPastAsof,
  supersectorLabel,
} from "../../util/format";
import {
  meterValueLabel,
  runwayCashWords,
  runwayNotApplicable,
  runwayPaceWords,
} from "../../workbench/format";
import { Meter } from "../../workbench/Meter";
import type { DashboardProps } from "../registry";
import {
  FILING_WINDOWS,
  GROUPS,
  ROLE_GLYPH,
  authorWords,
  capTitle,
  capWords,
  compactUsd,
  figureTitle,
  figuresOf,
  fillWords,
  firedWords,
  fmtDay,
  fmtDayHeader,
  fmtFigure,
  fmtPrice,
  foldEntries,
  foldWords,
  keyWords,
  labelTitle,
  notListedWords,
  placeWords,
  plural,
  rangeTrackInfo,
  ratifiedWords,
  roleTag,
  sourceLabel,
  sparkMarkers,
  stateWords,
  triggerCounts,
  usdHint,
  weightWords,
} from "./lookupView";
import type {
  LookupCard,
  LookupFiling,
  LookupFilingDay,
  LookupTrigger,
  TickerLookupOut,
  TickerLookupPriceWindowOut,
} from "./useTickerLookup";

// The Ticker Lookup's seven sections (docs/dashboards/ticker-lookup.md), each stating the n it rests on. Display
// only: every figure arrives computed; these draw it. Loudness marks the exception only (#7 / WB#3): the one accent
// is the "armed" word in a card's place cell, the sign tone only on signed tape figures, nothing bold but the
// ticker.

type OpenThesis = DashboardProps["onSelectThesis"];

export function Section({
  id,
  title,
  n,
  children,
}: {
  id?: string;
  title: string;
  n?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="tl-sec" aria-label={title}>
      <h2 className="tl-h">
        {title}
        {n && <span className="tl-n">{n}</span>}
      </h2>
      {children}
    </section>
  );
}

/** Compact section jump strip directly under Identity so long dossiers are one click to navigate. */
export function SectionNav({
  d,
  filingRows,
  eventCount = 0,
}: {
  d: TickerLookupOut;
  filingRows: number;
  eventCount?: number;
}) {
  const hasFundamentals = d.scored != null || d.shared_values != null;
  const items = [
    { id: "tl-sec-chart", label: "Chart", count: eventCount > 0 ? eventCount : null },
    { id: "tl-sec-tape", label: "Tape", count: null },
    ...(hasFundamentals ? [{ id: "tl-sec-fundamentals", label: "Fundamentals", count: null }] : []),
    { id: "tl-sec-cards", label: "Cards", count: d.cards.rows.length },
    { id: "tl-sec-triggers", label: "Triggers", count: d.triggers.events.length },
    { id: "tl-sec-filings", label: "Filings", count: filingRows },
    { id: "tl-sec-catalysts", label: "Catalysts", count: d.card_catalysts.length + d.catalyst_facts.length },
    { id: "tl-sec-decisions", label: "Decisions", count: d.decisions.length },
  ];
  const jump = (id: string) => {
    const el = document.getElementById(id);
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  return (
    <nav className="tl-jump" aria-label="Jump to section" data-testid="tl-jump">
      {items.map((it) => (
        <button key={it.id} type="button" className="tl-jump-link" onClick={() => jump(it.id)}>
          {it.label}
          {it.count != null && <span className="tl-jump-n">{it.count}</span>}
        </button>
      ))}
    </nav>
  );
}

// --- 1. identity -------------------------------------------------------------------------------------------------

export function IdentitySection({ d }: { d: TickerLookupOut }) {
  const i = d.identity;
  return (
    <section className="tl-sec tl-identity" aria-label="Identity">
      <div className="tl-title">
        <span className="tl-ticker">{i.ticker ?? "?"}</span>
        <span className="tl-name">{i.name ?? "unnamed company"}</span>
        {i.instrument_kind === "etf" && <span className="tl-tag">ETF</span>}
      </div>
      <dl className="tl-facts">
        <div>
          <dt>CIK</dt>
          <dd>
            {i.cik && i.edgar_url ? (
              <ExternalLink
                href={i.edgar_url}
                title={isSecLink(i.edgar_url) ? "Open the company on EDGAR" : "Open the stored source"}
              >
                {i.cik} ↗
              </ExternalLink>
            ) : (
              "—"
            )}
          </dd>
        </div>
        <div>
          <dt>Exchange</dt>
          <dd>{i.exchange ?? "—"}</dd>
        </div>
        <div>
          <dt>Business</dt>
          <dd>
            {businessTypeLabel(i.business_type)} · {supersectorLabel(i.supersector)}
            {i.royalty ? " · royalty/streaming" : ""}
          </dd>
        </div>
        <div>
          <dt>Origin</dt>
          <dd>{i.origin ?? "—"}</dd>
        </div>
        <div>
          <dt>Market cap</dt>
          <dd title={capTitle(i.cap)} data-testid="tl-cap">
            {capWords(i.cap)}
          </dd>
        </div>
        <div>
          <dt>$vol 20d</dt>
          <dd title={i.adv_note ?? undefined}>{i.adv_usd_20d != null ? compactUsd(i.adv_usd_20d) : "n/a"}</dd>
        </div>
        {i.price_symbol && (
          <div>
            <dt>Priced under</dt>
            <dd>{i.price_symbol}</dd>
          </div>
        )}
        {i.former_tickers.length > 0 && (
          <div>
            <dt>Formerly</dt>
            <dd>{i.former_tickers.join(", ")}</dd>
          </div>
        )}
      </dl>
      {i.flag && (
        <div className="tl-flag" data-testid="tl-flag">
          Data flag on its price readings: {i.flag}. The market cap is withheld and the price figures below are grayed.
        </div>
      )}
    </section>
  );
}

// --- 2. cards ----------------------------------------------------------------------------------------------------

function CardLine({ r, onSelectThesis }: { r: LookupCard; onSelectThesis: OpenThesis }) {
  const place = placeWords(r.place);
  const many = r.links.length > 1;
  const weight = weightWords(r.links);
  const fits = r.links.filter((k) => k.thesis_fit);
  return (
    <tr data-testid="tl-card">
      <td className="tl-lead">
        <button
          type="button"
          className="tl-card"
          title={`Open ${r.thesis_name} on ${r.ticker}`}
          onClick={() => onSelectThesis(r.thesis_id, r.ticker)}
        >
          {r.thesis_name}
        </button>
      </td>
      <td>{r.links.map((k) => k.link ?? "No link").join(" · ")}</td>
      <td className="tl-quiet">
        {[r.silent, r.context_role ? roleTag(r.context_role) : null].filter(Boolean).join(" · ") || "—"}
      </td>
      <td className="tl-quiet">{weight ?? "—"}</td>
      <td className="tl-quiet">{r.signed_off ? "signed off" : "—"}</td>
      <td className="tl-why">
        {fits.length === 0
          ? "—"
          : fits.map((k, idx) => (
              <span key={idx} className="tl-fit">
                {many ? `${k.link ?? "No link"}: ` : ""}
                {k.thesis_fit} <span className="tl-by">{authorWords(k.authored_by)}</span>
              </span>
            ))}
      </td>
      <td className="tl-quiet">{stateWords(r)}</td>
      <td>
        <span className="tl-place" data-armed={place.armed}>
          {place.armed ? (
            <>
              <span className="tl-armed">armed</span>
              {place.text.slice("armed".length)}
            </>
          ) : (
            place.text
          )}
        </span>
      </td>
    </tr>
  );
}

export function CardsSection({ d, onSelectThesis }: { d: TickerLookupOut; onSelectThesis: OpenThesis }) {
  const c = d.cards;
  return (
    <Section id="tl-sec-cards" title="Cards" n={`on ${c.rows.length} of ${plural(c.counted, "counted card", "counted cards")}`}>
      {c.rows.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-cards">
          Not on any counted card at this as-of. Its identity, tape and filings stand on their own below.
        </div>
      ) : (
        <div className="tl-scroll">
          <table className="tl-table">
            <thead>
              <tr>
                <th className="tl-lead">Card</th>
                <th>Link</th>
                <th>Part</th>
                <th>Weight</th>
                <th>Signed off</th>
                <th>Why it sits there</th>
                <th>Recorded call</th>
                <th>Place on the card</th>
              </tr>
            </thead>
            <tbody>
              {c.rows.map((r) => (
                <CardLine key={r.thesis_id} r={r} onSelectThesis={onSelectThesis} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {c.archived.length > 0 && (
        <details className="tl-fold" data-testid="tl-archived">
          <summary>also on {plural(c.archived.length, "archived card", "archived cards")}</summary>
          <ul>
            {c.archived.map((a) => (
              <li key={a.thesis_id}>{a.thesis_name}</li>
            ))}
          </ul>
        </details>
      )}
      {c.excluded.length > 0 && (
        <details className="tl-fold" data-testid="tl-excluded">
          <summary>excluded from {plural(c.excluded.length, "card", "cards")}</summary>
          <ul>
            {c.excluded.map((x) => (
              <li key={x.thesis_id}>
                {x.thesis_name}
                {x.reason ? `: ${x.reason}` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </Section>
  );
}

// --- 3. the record's triggers ------------------------------------------------------------------------------------

function TriggerLine({ e, onSelectThesis }: { e: LookupTrigger; onSelectThesis: OpenThesis }) {
  const role = ROLE_GLYPH[e.role];
  return (
    <tr data-testid="tl-trigger" data-live={e.live}>
      <td className="tl-num tl-lead">
        {fmtDay(e.recorded_on)}
        {e.reconstructed && <span className="tl-quiet"> reconstructed</span>}
      </td>
      <td className="tl-num">{firedWords(e)}</td>
      <td>
        <button
          type="button"
          className="tl-card"
          title={`Open ${e.thesis_name} on ${e.ticker}`}
          onClick={() => onSelectThesis(e.thesis_id, e.ticker)}
        >
          {e.thesis_name}
        </button>
      </td>
      <td className="tl-glyph" title={role.label} aria-label={role.label}>
        {role.glyph}
      </td>
      <td>{e.kind.replace(/_/g, " ")}</td>
      <td className="tl-quiet">{e.grade ?? "—"}</td>
      <td className="tl-label" title={labelTitle(e)}>
        {e.label}
      </td>
      <td className="tl-quiet">{keyWords(e)}</td>
      <td className="tl-src">
        {e.sources.length === 0
          ? "—"
          : e.sources.map((s, idx) =>
              s.url ? (
                <ExternalLink key={idx} href={s.url} title={s.detail.join("\n") || s.ref}>
                  {sourceLabel(s)} ↗
                </ExternalLink>
              ) : (
                <span key={idx} title={s.detail.join("\n") || s.ref}>
                  {sourceLabel(s)}
                </span>
              ),
            )}
      </td>
      <td className="tl-quiet" title={e.live ? undefined : "no longer on the card in effect at the as-of"}>
        {e.live ? "live" : "—"}
      </td>
    </tr>
  );
}

export function TriggersSection({ d, onSelectThesis }: { d: TickerLookupOut; onSelectThesis: OpenThesis }) {
  const t = d.triggers;
  const tc = triggerCounts(t.events);
  return (
    <Section
      id="tl-sec-triggers"
      title="Triggers the record shows"
      n={`${plural(t.events.length, "row", "rows")} since ${fmtDay(t.window_start)} · ${tc.live} live`}
    >
      {t.events.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-triggers">
          No trigger or risk signal on this company in the recorded calls of the cards it sits on, since{" "}
          {fmtDay(t.window_start)}
          {t.before_window_live > 0
            ? `; ${plural(t.before_window_live, "live trigger", "live triggers")} first recorded earlier are not listed`
            : ""}
          .
        </div>
      ) : (
        <div className="tl-scroll">
          <table className="tl-table">
            <thead>
              <tr>
                <th className="tl-lead">Recorded</th>
                <th>Fired</th>
                <th>Card</th>
                <th title="● entry trigger · ○ risk signal">Role</th>
                <th>Kind</th>
                <th>Grade</th>
                <th>Label</th>
                <th>Key</th>
                <th>Source</th>
                <th>Now</th>
              </tr>
            </thead>
            <tbody>
              {t.events.map((e, i) => (
                <TriggerLine
                  key={`${e.thesis_id}|${e.role}|${e.kind}|${e.event_date}|${i}`}
                  e={e}
                  onSelectThesis={onSelectThesis}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// --- 4. tape -----------------------------------------------------------------------------------------------------

const LWC_H = 210;
const LWC_AXIS = "#5a6470"; // --txt-3
const LWC_GRID = "#1e232c"; // --line
const LWC_BASE = "#3c4450"; // --txt-4
const LWC_STOCK = "#d8dee9"; // --txt
const LWC_POS = "#5fa87b"; // muted --pos
const LWC_NEG = "#b56565"; // muted --neg
const LWC_IWM = "#7892b5"; // muted --leader
const LWC_MARK: Record<"entry" | "risk" | "decision", string> = {
  entry: "#e0a23c", // --warm
  risk: "#c46b5c", // muted --armed
  decision: "#8ab4ff", // --leader
};

function fmtIdxPct(level: number | null | undefined): string {
  if (level == null) return "—";
  const pct = level - 100;
  if (Math.abs(pct) < 0.05) return "0.0%";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(1)}%`;
}

function TapeChart({ d, flagged }: { d: TickerLookupOut; flagged: boolean }) {
  const spark = d.tape.sparkline;
  const boxRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const markers = useMemo(
    () => (spark ? sparkMarkers(spark.dates, d.triggers.events, d.decisions) : []),
    [spark, d.triggers.events, d.decisions],
  );

  const hasStock = (spark?.stock.some((v) => v != null) ?? false);
  const hasIwm = (spark?.iwm.some((v) => v != null) ?? false);

  useEffect(() => {
    const el = boxRef.current;
    if (!el || !spark || spark.dates.length < 2 || (!hasStock && !hasIwm)) return;
    setHoverIdx(null);

    const width0 = el.clientWidth || 680;
    const chart = createChart(el, {
      width: width0,
      height: LWC_H,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: LWC_AXIS,
        fontSize: 10,
        fontFamily: "JetBrains Mono, ui-monospace, monospace",
      },
      grid: { vertLines: { visible: false }, horzLines: { color: LWC_GRID, style: LineStyle.Dotted } },
      rightPriceScale: { borderVisible: false },
      leftPriceScale: { visible: false },
      timeScale: { borderVisible: false, fixLeftEdge: true, fixRightEdge: true },
      crosshair: {
        vertLine: { color: LWC_AXIS, width: 1, style: LineStyle.Dashed, labelBackgroundColor: "#171b22" },
        horzLine: { color: LWC_BASE, width: 1, style: LineStyle.Dotted, labelBackgroundColor: "#171b22" },
      },
      handleScroll: true,
      handleScale: true,
    });
    chartRef.current = chart;

    if (hasIwm) {
      const iwmSeries = chart.addLineSeries({
        color: LWC_IWM,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerRadius: 3,
      });
      iwmSeries.setData(
        spark.dates
          .map((dt, j) => (spark.iwm[j] != null ? { time: dt as Time, value: spark.iwm[j] as number } : null))
          .filter((p): p is { time: Time; value: number } => p !== null),
      );
    }

    const lastStock = [...spark.stock].reverse().find((v) => v != null) ?? null;
    const stockColor =
      flagged ? LWC_AXIS : lastStock != null && lastStock > 100.05 ? LWC_POS : lastStock != null && lastStock < 99.95 ? LWC_NEG : LWC_STOCK;
    const stockSeries = chart.addLineSeries({
      color: stockColor,
      lineWidth: 2,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 4,
    });
    if (hasStock) {
      stockSeries.setData(
        spark.dates
          .map((dt, j) => (spark.stock[j] != null ? { time: dt as Time, value: spark.stock[j] as number } : null))
          .filter((p): p is { time: Time; value: number } => p !== null),
      );
    }
    stockSeries.createPriceLine({
      price: 100,
      color: LWC_BASE,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "100",
    });

    if (markers.length > 0) {
      const lwMarkers: SeriesMarker<Time>[] = [...markers]
        .sort((a, b) => a.date.localeCompare(b.date))
        .map((m) => ({
          time: m.date as Time,
          position: m.kind === "risk" ? "aboveBar" : "belowBar",
          shape: m.kind === "decision" ? "arrowUp" : "circle",
          color: LWC_MARK[m.kind],
          text: m.kind === "decision" ? "D" : "",
          size: 1,
        }));
      stockSeries.setMarkers(lwMarkers);
    }

    chart.timeScale().fitContent();

    const dateToIdx = new Map(spark.dates.map((dt, j) => [dt, j]));
    const onCrosshair = (param: { time?: Time }) => {
      if (!param.time) {
        setHoverIdx(null);
        return;
      }
      const key = typeof param.time === "string" ? param.time : null;
      const found = key != null ? dateToIdx.get(key) : undefined;
      setHoverIdx(found ?? null);
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
  }, [spark, markers, flagged, hasStock, hasIwm]);

  if (!spark || spark.dates.length < 2 || (!hasStock && !hasIwm)) return null;

  const n = spark.dates.length;
  const idx = hoverIdx ?? n - 1;
  const stockVal = spark.stock[idx] ?? null;
  const iwmVal = spark.iwm[idx] ?? null;
  const spread = stockVal != null && iwmVal != null ? stockVal - iwmVal : null;
  const atMarkers = markers.filter((m) => m.index === idx);

  return (
    <div className={`tl-group tl-spark${flagged ? " flagged" : ""}`} data-group="path">
      <div className="tl-spark-head">
        <h3 className="tl-gh">Last {spark.bars} sessions</h3>
        <div className="tl-spark-readout" data-testid="tl-spark-readout">
          {n > 0 && (
            <>
              <span className="tl-sr-date">{fmtDay(spark.dates[idx])}</span>
              <span className="tl-sr-stock">
                {d.identity.ticker ?? "Stock"} {fmtIdxPct(stockVal)}
                {stockVal != null ? ` (${stockVal.toFixed(1)})` : ""}
              </span>
              {spark.iwm_bars > 0 && (
                <span className="tl-sr-iwm">
                  IWM {fmtIdxPct(iwmVal)}
                  {iwmVal != null ? ` (${iwmVal.toFixed(1)})` : ""}
                </span>
              )}
              {spread != null && (
                <span className={`tl-sr-spread${spread > 0 ? " pos" : spread < 0 ? " neg" : ""}`}>
                  vs IWM {spread > 0 ? "+" : ""}
                  {spread.toFixed(1)}pp
                </span>
              )}
            </>
          )}
        </div>
        <button
          type="button"
          className="tl-spark-reset"
          title="Reset pan and zoom to full window"
          onClick={() => chartRef.current?.timeScale().fitContent()}
        >
          Reset zoom
        </button>
      </div>
      <div className="tl-lwc-wrap">
        <div
          ref={boxRef}
          className="tl-lwc-box"
          role="img"
          aria-label={`The last ${spark.bars} closes, indexed to 100, against IWM`}
          data-testid="tl-spark"
        />
        {atMarkers.length > 0 && (
          <div className="tl-spark-events" aria-live="off">
            {atMarkers.map((m, i) => (
              <span key={i} className={`tl-spark-ev ${m.kind}`} data-testid="tl-spark-marker">
                {m.label}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="tl-spark-cap">
        the company (solid) and IWM (dashed blue), both indexed to 100 · scroll to zoom, drag to pan
        {markers.length > 0 ? ` · ${plural(markers.length, "event mark", "event marks")} (● trigger, ▲ decision)` : ""}
        {spark.iwm_bars === 0
          ? "; no IWM bars on file"
          : spark.iwm_bars < spark.bars
            ? `; IWM has ${spark.iwm_bars} of ${spark.bars} bars`
            : ""}
        {spark.note ? ` · ${spark.note}` : ""}
      </div>
    </div>
  );
}

const CHART_SPANS: ChartSpan[] = ["3M", "6M", "1Y", "ALL"];

export function ChartPanel({
  d,
  priceWindow,
  priceWindowLoading,
  priceWindowError,
  events,
  span,
  onSpan,
  activeN,
  onActivate,
}: {
  d: TickerLookupOut;
  priceWindow: TickerLookupPriceWindowOut | undefined;
  priceWindowLoading: boolean;
  priceWindowError: boolean;
  events: OverlayEvent[];
  span: ChartSpan;
  onSpan: (s: ChartSpan) => void;
  activeN: number | null;
  onActivate: (n: number | null) => void;
}) {
  const [mode, setMode] = useState<"events" | "iwm">("events");
  const flagged = d.identity.flag != null;
  const bars = priceWindow?.bars ?? [];
  const hasBars = bars.length >= 2;
  const spark = d.tape.sparkline;
  const hasSpark = Boolean(
    spark && spark.dates.length >= 2 && (spark.stock.some((v) => v != null) || spark.iwm.some((v) => v != null)),
  );
  // Default to Price & events when bars are loaded or loading; fall back to vs IWM if only sparkline exists.
  const showEvents = mode === "events" && (hasBars || priceWindowLoading || !hasSpark);

  if (!hasBars && !priceWindowLoading && !priceWindowError && !hasSpark) return null;

  return (
    <section
      id="tl-sec-chart"
      className={`tl-sec tl-chart-panel${flagged ? " flagged" : ""}`}
      aria-label="Price chart"
    >
      <div className="tl-chart-head">
        <h2 className="tl-h tl-chart-title">
          {showEvents ? "Price & events" : "Relative to IWM"}
          {showEvents && events.length > 0 && <span className="tl-n">{plural(events.length, "event", "events")}</span>}
        </h2>
        <div className="tl-chart-controls">
          {hasSpark && (hasBars || priceWindowLoading) && (
            <div className="tl-seg" role="group" aria-label="Chart mode">
              <button
                type="button"
                className={`tl-seg-btn${showEvents ? " on" : ""}`}
                aria-pressed={showEvents}
                onClick={() => setMode("events")}
              >
                Price &amp; events
              </button>
              <button
                type="button"
                className={`tl-seg-btn${!showEvents ? " on" : ""}`}
                aria-pressed={!showEvents}
                onClick={() => setMode("iwm")}
              >
                vs IWM
              </button>
            </div>
          )}
          {showEvents && hasBars && (
            <div className="tl-seg" role="group" aria-label="Chart range">
              {CHART_SPANS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className={`tl-seg-btn${span === s ? " on" : ""}`}
                  aria-pressed={span === s}
                  onClick={() => onSpan(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
      {showEvents ? (
        <>
          <PriceSparkline
            span={span}
            bars={bars}
            events={events}
            activeN={activeN}
            onActivate={onActivate}
            loading={priceWindowLoading}
            error={priceWindowError}
            hideIngest
          />
          {priceWindow && (
            <div className="tl-spark-cap">
              tape: {priceWindow.source} · loaded from {fmtDate(priceWindow.start)} · as-of {fmtDate(priceWindow.asof)}
              {flagged ? " · price data flag active" : ""}
            </div>
          )}
        </>
      ) : (
        <TapeChart d={d} flagged={flagged} />
      )}
    </section>
  );
}

export function TapeSection({ d, hideChart = false }: { d: TickerLookupOut; hideChart?: boolean }) {
  const tape = d.tape;
  const flagged = d.identity.flag != null;
  const rangeInfo = useMemo(() => rangeTrackInfo(tape.figures), [tape.figures]);
  const retFigs = useMemo(() => figuresOf(tape.figures, "returns"), [tape.figures]);
  const maxRet = useMemo(
    () => Math.max(5, ...retFigs.map((f) => (f.value != null ? Math.abs(f.value) : 0))),
    [retFigs],
  );
  return (
    <Section
      id="tl-sec-tape"
      title="Tape"
      n={`${tape.members_read - tape.members_without} of ${tape.members_read} display members read${tape.window_end ? ` · last bar ${fmtDay(tape.window_end)}` : ""}`}
    >
      {tape.figures.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-tape">
          No price tape on file at this as-of.
        </div>
      ) : (
        <div className="tl-tape">
          {!hideChart && <TapeChart d={d} flagged={flagged} />}
          {GROUPS.map(({ group, label, price }) => {
            const figs = figuresOf(tape.figures, group);
            if (figs.length === 0) return null;
            return (
              <div key={group} className={`tl-group${price && flagged ? " flagged" : ""}`} data-group={group}>
                <h3 className="tl-gh">{label}</h3>
                {group === "range" && rangeInfo && (
                  <div className="tl-range" data-testid="tl-range-track">
                    <div className="tl-range-ends">
                      <span>52w low {fmtPrice(rangeInfo.low)}</span>
                      <span>{Math.round(rangeInfo.pct)}% of range</span>
                      <span>52w high {fmtPrice(rangeInfo.high)}</span>
                    </div>
                    <div className="tl-range-bar" aria-hidden="true">
                      <span className="tl-range-fill" style={{ width: `${rangeInfo.pct}%` }} />
                      <span className="tl-range-dot" style={{ left: `${rangeInfo.pct}%` }} />
                    </div>
                  </div>
                )}
                <dl className="tl-figs">
                  {figs.map((f) => {
                    const showRetBar = group === "returns" && f.value != null && !flagged;
                    const pctW = showRetBar ? Math.min(50, (Math.abs(f.value!) / maxRet) * 50) : 0;
                    return (
                      <div
                        key={f.key}
                        className={`tl-fig${f.tone ? ` ${f.tone}` : ""}${f.value == null ? " na" : ""}`}
                        title={figureTitle(f)}
                        data-testid="tl-fig"
                        data-key={f.key}
                      >
                        <dt>{f.label}</dt>
                        <dd>
                          {fmtFigure(f)}
                          {showRetBar && (
                            <span className="tl-retbar" aria-hidden="true">
                              <span className="tl-retbar-zero" />
                              <span
                                className={`tl-retbar-fill ${f.tone ?? ""}`}
                                style={
                                  f.value! >= 0
                                    ? { left: "50%", width: `${pctW}%` }
                                    : { right: "50%", width: `${pctW}%` }
                                }
                              />
                            </span>
                          )}
                          {f.on && <small className="tl-on">{fmtDay(f.on)}</small>}
                          {f.value == null && f.note && <small className="tl-on">{f.note}</small>}
                        </dd>
                      </div>
                    );
                  })}
                </dl>
                {group === "strength" && tape.leading.length > 0 && (
                  <div className="tl-mark" data-testid="tl-leading">
                    leading: relative strength at a 13-week high vs {tape.leading.join(" and ")}
                  </div>
                )}
                {group === "strength" && tape.coiling && (
                  <div className="tl-mark" data-testid="tl-coiling">
                    base coiling{tape.coiling_detail ? `: ${tape.coiling_detail}` : ""}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
      {tape.gaps.length > 0 && (
        <ul className="tl-gaps" data-testid="tl-gaps">
          {tape.gaps.map((g) => (
            <li key={g.member}>
              No reading: {g.label} ({g.note})
            </li>
          ))}
        </ul>
      )}
      {tape.headline_notes.length > 0 && (
        <ul className="tl-gaps">
          {tape.headline_notes.map((n) => (
            <li key={n}>{n}</li>
          ))}
        </ul>
      )}
    </Section>
  );
}

// --- 4b. fundamentals, dilution & shared values ------------------------------------------------------------------

export function FundamentalsSection({ d }: { d: TickerLookupOut }) {
  const { currentNight } = useAppClock();
  const scored = d.scored ?? null;
  const values = d.shared_values ?? null;
  if (!scored && !values) return null;

  const isLive = !isPastAsof(d.asof, currentNight);
  const sharedNotes: string[] = [];
  if (isLive && values) {
    for (const [title, v] of [
      ["Share count", values.shares],
      ["Cash", values.cash_burn],
    ] as const) {
      if (v.state === "awaiting_steward") sharedNotes.push(`${title}: awaiting the steward`);
      else if (v.state === "no_value" && v.reason !== "cash-generative")
        sharedNotes.push(`${title}: no value yet (${reasonClause(v)})`);
      else if (v.pending) sharedNotes.push(`${title}: ${lowerFirst(v.pending.line)}`);
    }
  }
  if (isLive && scored && scored.instrument_kind !== "etf" && scored.purity.value == null) {
    sharedNotes.push("Purity: not set yet (yours to set in the Workbench)");
  }

  return (
    <Section
      id="tl-sec-fundamentals"
      title="Fundamentals & dilution"
      n={scored?.fit && scored.fit !== "unrated" ? scored.fit : undefined}
    >
      <div className="tl-fund-grid" data-testid="tl-fundamentals">
        {scored && (
          <div className="tl-fund-meters">
            <div className="np-meters">
              {(
                [
                  ["Runway", "runway", false],
                  ["Dilution", "dilution", true],
                  ["Catalysts", "catalysts", false],
                  ["Purity", "purity", false],
                ] as const
              ).map(([label, key, risk]) => {
                const pace = key === "runway" ? runwayPaceWords(scored[key]) : null;
                const cash = key === "runway" ? runwayCashWords(scored[key]) : null;
                const na = key === "runway" ? runwayNotApplicable(scored[key]) : null;
                return (
                  <Fragment key={key}>
                    <div className="np-meter-row">
                      <Meter
                        label={label}
                        figure={scored[key]}
                        risk={risk}
                        title={na ?? undefined}
                        notApplicable={na != null ? "n/a" : undefined}
                      />
                      <span className="val">{meterValueLabel(key, scored[key])}</span>
                    </div>
                    {na && (
                      <div className="np-pace" data-testid="tl-runway-na">
                        {na}
                      </div>
                    )}
                    {cash && (
                      <div className="np-pace" data-testid="tl-runway-cash">
                        {cash}
                      </div>
                    )}
                    {pace && (
                      <div className="np-pace" data-testid="tl-runway-pace">
                        {pace.ytd}
                      </div>
                    )}
                    {pace?.caveat && (
                      <div className="np-pace" data-testid="tl-runway-caveat">
                        {pace.caveat}
                      </div>
                    )}
                    <ProvenanceSources sources={scored[key].provenance ?? []} label={`${label} provenance`} />
                  </Fragment>
                );
              })}
            </div>
          </div>
        )}
        <div className="tl-fund-meta">
          {values && (
            <dl className="tl-fund-shared">
              {(
                [
                  ["Shares", values.shares],
                  ["Cash & burn", values.cash_burn],
                ] as const
              ).map(([title, v]) => (
                <div key={title} className="tl-fund-row">
                  <dt>{title}</dt>
                  <dd>
                    <span>{v.label}</span>
                    {v.value?.source_url && (
                      <ExternalLink className="tl-fsrc" href={v.value.source_url}>
                        {isSecLink(v.value.source_url) ? "EDGAR" : "source"} ↗
                      </ExternalLink>
                    )}
                  </dd>
                </div>
              ))}
            </dl>
          )}
          {scored && (scored.market_cap.provenance?.length ?? 0) > 0 && (
            <ProvenanceSources sources={scored.market_cap.provenance ?? []} label="Market cap provenance" />
          )}
          {scored?.thin_price_history && (
            <div className="np-thinflag">⚠ thin price history — history-window signals may be starved</div>
          )}
          {scored?.price_feed_limitation && (
            <div className="np-feednote" title={scored.price_feed_limitation}>
              price history limited: vendor serves no history
            </div>
          )}
          {sharedNotes.length > 0 && (
            <div className="tl-fund-notes">
              {sharedNotes.map((line) => (
                <div key={line}>{line}</div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Section>
  );
}

// --- 5. filings --------------------------------------------------------------------------------------------------

function FilingLine({
  row,
  open,
  onToggle,
}: {
  row: LookupFiling;
  open: boolean;
  onToggle: () => void;
}) {
  const hint = usdHint(row);
  const ratified = ratifiedWords(row);
  const expandable = row.detail.length > 0;
  const source = isSecLink(row.url) ? "EDGAR" : "source";
  return (
    <li className={`tl-frow${open ? " open" : ""}`} data-testid="tl-filing" data-kind={row.kind}>
      <div className="tl-fline">
        <span className="tl-form">{row.form}</span>
        <span className="tl-fmain">
          {expandable ? (
            <button type="button" className="tl-fhead" aria-expanded={open} onClick={onToggle}>
              <span className="tl-chev" aria-hidden="true">
                {open ? "▾" : "▸"}
              </span>
              {row.headline}
            </button>
          ) : (
            <span className="tl-fhead plain">{row.headline}</span>
          )}
          {row.tags.map((tag) => (
            <span key={tag} className="tl-ftag">
              {tag}
            </span>
          ))}
          {ratified && <span className="tl-ftag">{ratified}</span>}
        </span>
        <span className="tl-usd">{hint}</span>
        {row.url ? (
          <ExternalLink
            className="tl-fsrc"
            href={row.url}
            ariaLabel={`Open ${row.form} on ${source === "EDGAR" ? "EDGAR" : "its source"}`}
          >
            {source} ↗
          </ExternalLink>
        ) : (
          <span className="tl-fsrc none" />
        )}
      </div>
      {open && (
        <ul className="tl-fdetail" data-testid="tl-fdetail">
          {row.detail.map((line, i) => (
            <li key={i}>{line}</li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function FilingsSection({
  days,
  rowCount,
  windowN,
  onWindow,
  d,
}: {
  days: LookupFilingDay[];
  rowCount: number;
  windowN: number;
  onWindow: (n: number) => void;
  d: TickerLookupOut;
}) {
  const [showRoutine, setShowRoutine] = useState(false);
  const [pickedKind, setPickedKind] = useState<string | null>(null);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [foldOpen, setFoldOpen] = useState<ReadonlySet<string>>(new Set());
  const toggle = (set: ReadonlySet<string>, k: string, put: (s: ReadonlySet<string>) => void) => {
    const next = new Set(set);
    if (next.has(k)) next.delete(k);
    else next.add(k);
    put(next);
  };

  // Distinct kinds present in the current window for click-to-isolate filtering (WB#3: only when > 1)
  const kinds = useMemo(() => {
    const counts = new Map<string, number>();
    for (const day of days) {
      for (const r of day.rows) counts.set(r.kind, (counts.get(r.kind) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [days]);
  const activeKind = pickedKind && kinds.some(([k]) => k === pickedKind) ? pickedKind : null;
  const shownDays = useMemo(() => {
    if (!activeKind) return days;
    return days
      .map((day) => ({ ...day, rows: day.rows.filter((r) => r.kind === activeKind) }))
      .filter((day) => day.rows.length > 0);
  }, [days, activeKind]);
  const shownCount = useMemo(() => shownDays.reduce((acc, day) => acc + day.rows.length, 0), [shownDays]);

  const hasRoutine = shownDays.some((x) => x.rows.some((r) => r.routine));
  const notListed = notListedWords(d.filings);
  return (
    <Section
      id="tl-sec-filings"
      title="Filings"
      n={
        activeKind
          ? `${plural(shownCount, "row", "rows")} of ${rowCount} (${activeKind}) in the last ${windowN} days`
          : `${plural(rowCount, "row", "rows")} in the last ${windowN} days`
      }
    >
      <div className="tl-controls">
        <div className="tl-seg" role="group" aria-label="Filings window">
          <span className="tl-seg-label">Window</span>
          {FILING_WINDOWS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`tl-seg-btn${windowN === o.value ? " on" : ""}`}
              aria-pressed={windowN === o.value}
              onClick={() => onWindow(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        {kinds.length > 1 && (
          <div className="tl-seg" role="group" aria-label="Filing kind">
            <span className="tl-seg-label">Kind</span>
            <button
              type="button"
              className={`tl-seg-btn${activeKind == null ? " on" : ""}`}
              aria-pressed={activeKind == null}
              onClick={() => setPickedKind(null)}
            >
              All
            </button>
            {kinds.map(([k, n]) => (
              <button
                key={k}
                type="button"
                className={`tl-seg-btn${activeKind === k ? " on" : ""}`}
                aria-pressed={activeKind === k}
                onClick={() => setPickedKind((cur) => (cur === k ? null : k))}
              >
                {k.replace(/_/g, " ")} <span className="tl-chip-n">{n}</span>
              </button>
            ))}
          </div>
        )}
        {hasRoutine && (
          <label
            className="tl-check"
            title="Show each routine row in place instead of one folded line per kind and day"
          >
            <input type="checkbox" checked={showRoutine} onChange={(e) => setShowRoutine(e.target.checked)} />
            Show routine rows
          </label>
        )}
      </div>
      {shownDays.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-filings">
          No stored filing-derived fact for this company in the last {windowN} days.
        </div>
      ) : (
        <div className="tl-stream">
          {shownDays.map((day) => (
            <section key={day.date} className="tl-day" aria-label={fmtDayHeader(day.date)}>
              <h3 className="tl-dh">
                {fmtDayHeader(day.date)} <span className="tl-n">{plural(day.rows.length, "row", "rows")}</span>
              </h3>
              <ul className="tl-flist">
                {foldEntries(day.rows, showRoutine).map((entry, idx) => {
                  const line = (row: LookupFiling, j: number) => {
                    const key = `${day.date}|${row.accession ?? row.headline}|${idx}|${j}`;
                    return <FilingLine key={key} row={row} open={open.has(key)} onToggle={() => toggle(open, key, setOpen)} />;
                  };
                  if (entry.type === "row") return line(entry.row, 0);
                  const foldKey = `${day.date}|${entry.group}`;
                  const isOpen = foldOpen.has(foldKey);
                  return (
                    <li key={`fold-${foldKey}`} className="tl-fold-item" data-fold={entry.group}>
                      <button
                        type="button"
                        className="tl-foldbtn"
                        aria-expanded={isOpen}
                        onClick={() => toggle(foldOpen, foldKey, setFoldOpen)}
                      >
                        <span className="tl-chev" aria-hidden="true">
                          {isOpen ? "▾" : "▸"}
                        </span>
                        {foldWords(entry)}
                      </button>
                      {isOpen && <ul className="tl-flist tl-folded">{entry.rows.map(line)}</ul>}
                    </li>
                  );
                })}
              </ul>
            </section>
          ))}
        </div>
      )}
      {notListed && <div className="dash-note tl-work">{notListed}</div>}
    </Section>
  );
}

// --- 6. catalysts ------------------------------------------------------------------------------------------------

export function CatalystsSection({ d, onSelectThesis }: { d: TickerLookupOut; onSelectThesis: OpenThesis }) {
  const origin = useCountdownOrigin(d.asof);
  const facts = d.catalyst_facts;
  const cards = d.card_catalysts;
  return (
    <Section
      id="tl-sec-catalysts"
      title="Catalysts"
      n={`${plural(cards.length, "card catalyst", "card catalysts")} · ${plural(facts.length, "company fact", "company facts")}`}
    >
      <h3 className="tl-gh">Of the cards it counts on</h3>
      {cards.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-card-catalysts">
          No catalyst listed on a card this company counts on.
        </div>
      ) : (
        <ul className="tl-clist">
          {cards.map((c, i) => {
            const delta = daysFrom(origin, c.when_date);
            const soon = !c.passed && delta !== null && delta >= 0 && delta <= 21;
            const cls = [c.passed ? "passed" : "", soon ? "soon" : ""].filter(Boolean).join(" ") || undefined;
            return (
              <li key={`${c.thesis_id}|${c.label}|${i}`} className={cls} data-testid="tl-catalyst">
                <span className="tl-num tl-cat-when">
                  {c.when_date ? fmtDay(c.when_date) : (c.when_label ?? "undated")}
                  {!c.passed && delta !== null && delta >= 0 && (
                    <span className="tl-cat-cd"> · {dayCount(delta)}</span>
                  )}
                </span>
                <span className="tl-clabel">{c.label}</span>
                {c.kind && <span className="tl-quiet">{c.kind}</span>}
                {c.passed && <span className="tl-quiet">passed</span>}
                <button type="button" className="tl-card" onClick={() => onSelectThesis(c.thesis_id, d.identity.ticker)}>
                  {c.thesis_name}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <h3 className="tl-gh">Its own catalyst facts</h3>
      {facts.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-facts">
          No catalyst fact stored for this company.
        </div>
      ) : (
        <ul className="tl-clist">
          {facts.map((f, i) => {
            const ratified = ratifiedWords(f);
            return (
              <li
                key={`${f.accession ?? f.headline}|${i}`}
                className={f.live ? "live" : undefined}
                data-testid="tl-catalyst-fact"
              >
                <span className="tl-num">{fmtDay(f.date)}</span>
                <span className="tl-clabel">
                  {f.headline}
                  {f.live && <span className="tl-cat-live">live</span>}
                </span>
                {ratified && <span className="tl-quiet">{ratified}</span>}
                {f.url && <ExternalLink href={f.url}>source ↗</ExternalLink>}
              </li>
            );
          })}
        </ul>
      )}
    </Section>
  );
}

// --- 7. decisions ------------------------------------------------------------------------------------------------

export function DecisionsSection({ d, onSelectThesis }: { d: TickerLookupOut; onSelectThesis: OpenThesis }) {
  const rows = d.decisions;
  return (
    <Section
      id="tl-sec-decisions"
      title="Your decisions"
      n={`${plural(rows.length, "row", "rows")}${d.decisions_voided > 0 ? ` · ${d.decisions_voided} voided` : ""}`}
    >
      {rows.length === 0 ? (
        <div className="dash-note" data-testid="tl-no-decisions">
          No decision logged on this company as of {fmtDay(d.asof)}.
        </div>
      ) : (
        <div className="tl-scroll">
          <table className="tl-table">
            <thead>
              <tr>
                <th className="tl-lead">Date</th>
                <th>Card</th>
                <th>Action</th>
                <th>Fill</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((x) => (
                <tr key={x.id} className={x.voided ? "voided" : undefined} data-testid="tl-decision" data-voided={x.voided}>
                  <td className="tl-num tl-lead">{fmtDay(x.decision_date)}</td>
                  <td>
                    <button
                      type="button"
                      className="tl-card"
                      onClick={() => onSelectThesis(x.thesis_id, d.identity.ticker)}
                    >
                      {x.thesis_name}
                    </button>
                  </td>
                  <td>
                    <span className={x.voided ? "tl-struck" : undefined}>{x.action}</span>
                    {x.voided && <span className="tl-quiet"> voided</span>}
                  </td>
                  <td className="tl-num">
                    <span className={x.voided ? "tl-struck" : undefined}>{fillWords(x)}</span>
                  </td>
                  <td className="tl-why">{x.reason ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}
