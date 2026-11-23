import { useMemo, useState, type KeyboardEvent } from "react";

import { FlaggedNote, flaggedRule } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./narrative-adoption.css";
import {
  DOC_PANEL,
  FILER_PANEL,
  FRAME,
  GROUPS,
  INDEX_PANEL,
  PLOT_W,
  axisLabels,
  baseY,
  januarySlots,
  layoutBars,
  layoutFilers,
  layoutOverlay,
  lastCountedIndex,
  overlayPointAtMonth,
  plotHeight,
  slotCenter,
  slotWidth,
  slotX,
  stepMonth,
  type BarMode,
  type Group,
  type Panel,
} from "./adoptionLayout";
import {
  GROUP_FORMS,
  GROUP_LABEL,
  NOT_COUNTED,
  filersWords,
  filterCards,
  firstWords,
  fmtDay,
  fmtMonth,
  monthReadout,
  nextTermSort,
  plural,
  sortTerms,
  windowWords,
  workLine,
  type TermSort,
  type TermSortKey,
} from "./adoptionView";
import { useAdoptionCards, useNarrativeAdoption, type AdoptionOut } from "./useNarrativeAdoption";

// Narrative Adoption (docs/dashboards/narrative-adoption.md): how many filings mention ONE card's SIGNAL terms each
// month over three years. Three stacked panels on one 36-month axis (documents by form group, filers and new
// filers, the card's basket line against SPY), the term table below. Plain inline SVG laid out by the pure
// adoptionLayout.ts; no chart library. Display only. Every number is computed server-side; this page picks the
// card, lays the panels out and words them. Neutral by design: no badge, no "early" or "crowded", four muted fills.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";
const SLOW = "Reading this card's counts — the first load after a run or a basket edit can take a moment.";
const PROMPT =
  "Hover a month for its counts (click to pin): the form groups, the terms with the most documents, the filers and the tape. The left and right arrow keys step through the months, Home and End jump to the first and last, Escape clears.";

const compact = (v: number): string => (v >= 1000 ? `${Number((v / 1000).toFixed(1))}k` : String(Number(v.toFixed(1))));

// --- shared parts of the three panels ------------------------------------------------------------------------

type PanelProps = {
  axis: readonly string[];
  panel: Panel;
  cursor: number | null;
  pinned: number | null;
  onHover: (i: number) => void;
  onPin: (i: number) => void;
};

/** The column behind the month under the cursor, the January rules, and the transparent hit slots. */
function Backdrop({ axis, panel, cursor, pinned }: Pick<PanelProps, "axis" | "panel" | "cursor" | "pinned">) {
  const n = axis.length;
  return (
    <>
      {januarySlots(axis).map((i) => (
        <line
          key={i}
          className="na-rule"
          x1={slotX(i, n)}
          x2={slotX(i, n)}
          y1={panel.top}
          y2={baseY(panel)}
        />
      ))}
      {cursor != null && (
        <>
          <rect
            className={`na-cursor${pinned === cursor ? " pinned" : ""}`}
            data-testid="na-cursor"
            x={slotX(cursor, n)}
            y={panel.top}
            width={slotWidth(n)}
            height={plotHeight(panel)}
          />
          <line
            className="na-cursor-line"
            x1={slotCenter(cursor, n)}
            x2={slotCenter(cursor, n)}
            y1={panel.top}
            y2={baseY(panel)}
          />
        </>
      )}
    </>
  );
}

function HitSlots({ axis, panel, onHover, onPin }: Pick<PanelProps, "axis" | "panel" | "onHover" | "onPin">) {
  const n = axis.length;
  return (
    <>
      {axis.map((m, i) => (
        <rect
          key={m}
          className="na-hit"
          data-testid="na-hit"
          data-index={i}
          x={slotX(i, n)}
          y={panel.top}
          width={slotWidth(n)}
          height={plotHeight(panel)}
          onMouseEnter={() => onHover(i)}
          onClick={() => onPin(i)}
        />
      ))}
    </>
  );
}

function YTicks({ ticks, format }: { ticks: readonly { v: number; y: number }[]; format?: (v: number) => string }) {
  return (
    <>
      {ticks.map((t) => (
        <g key={t.v}>
          <line className={t.v === 0 ? "na-base" : "na-grid"} x1={FRAME.left} x2={FRAME.left + PLOT_W} y1={t.y} y2={t.y} />
          <text className="na-axis" x={FRAME.left - 6} y={t.y + 3} textAnchor="end">
            {(format ?? compact)(t.v)}
          </text>
        </g>
      ))}
    </>
  );
}

// --- panel 1: documents ----------------------------------------------------------------------------------------

function DocumentsPanel({
  data,
  activeGroups,
  mode,
  ...p
}: PanelProps & { data: AdoptionOut; activeGroups: ReadonlySet<Group>; mode: BarMode }) {
  const chart = useMemo(
    () =>
      layoutBars(data.months, p.panel, {
        groups: activeGroups,
        mode,
        signalTerms: data.counts.signal_terms,
      }),
    [data.months, p.panel, activeGroups, mode, data.counts.signal_terms],
  );
  const none = data.counts.months_counted === 0;
  return (
    <svg
      className="na-svg"
      data-testid="na-panel-docs"
      viewBox={`0 0 ${FRAME.w} ${p.panel.h}`}
      aria-hidden="true"
    >
      <YTicks ticks={chart.ticks} format={mode === "share" ? (v) => `${Math.round(v)}%` : undefined} />
      <Backdrop {...p} />
      {chart.unclosed.map((u) => (
        <rect
          key={`u${u.index}`}
          className="na-unclosed"
          data-testid="na-unclosed"
          x={u.x}
          y={chart.topY + 2}
          width={u.w}
          height={Math.max(2, chart.baseY - chart.topY - 2)}
        />
      ))}
      {chart.bars.map((b) => (
        <g key={b.index} data-testid="na-bar" data-index={b.index}>
          {b.zero ? (
            <rect className="na-zero" data-testid="na-zero" x={b.x} y={chart.baseY - 1.5} width={b.w} height={1.5} />
          ) : (
            <>
              {b.segments.map((s) => (
                <rect key={s.group} className={`na-seg na-g-${s.group}`} x={b.x} y={s.y} width={b.w} height={s.h} />
              ))}
              {b.partial && (
                <line
                  className="na-partial-cap"
                  data-testid="na-partial-cap"
                  x1={b.x}
                  x2={b.x + b.w}
                  y1={Math.max(chart.topY, b.topY - 2)}
                  y2={Math.max(chart.topY, b.topY - 2)}
                />
              )}
            </>
          )}
        </g>
      ))}
      {none && (
        <text className="na-empty-text" x={FRAME.left + PLOT_W / 2} y={(p.panel.top + chart.baseY) / 2} textAnchor="middle">
          no month counted yet
        </text>
      )}
      <HitSlots {...p} />
    </svg>
  );
}

// --- panel 2: filers -----------------------------------------------------------------------------------------

function FilersPanel({ data, ...p }: PanelProps & { data: AdoptionOut }) {
  const chart = useMemo(() => layoutFilers(data.months), [data.months]);
  const none = data.counts.months_counted === 0;
  const activeFiler = p.cursor != null ? chart.filers.points.find((pt) => pt.index === p.cursor) : undefined;
  const activeFresh = p.cursor != null ? chart.newFilers.points.find((pt) => pt.index === p.cursor) : undefined;
  return (
    <svg
      className="na-svg"
      data-testid="na-panel-filers"
      viewBox={`0 0 ${FRAME.w} ${p.panel.h}`}
      aria-hidden="true"
    >
      <YTicks ticks={chart.ticks} />
      <Backdrop {...p} />
      {chart.filers.path && <path className="na-line na-l-filers" data-testid="na-filers-line" d={chart.filers.path} />}
      {chart.newFilers.path && <path className="na-line na-l-fresh" data-testid="na-new-line" d={chart.newFilers.path} />}
      {chart.newFilers.points.map((pt) => (
        <circle key={`n${pt.index}`} className="na-dot na-l-fresh" cx={pt.x} cy={pt.y} r={1.6} />
      ))}
      {chart.filers.points.map((pt) => (
        <g key={`f${pt.index}`}>
          <circle className="na-dot na-l-filers" cx={pt.x} cy={pt.y} r={2} />
          {pt.ring && <circle className="na-ring" data-testid="na-ring" cx={pt.x} cy={pt.y} r={3.8} />}
        </g>
      ))}
      {activeFresh && <circle className="na-dot-active na-l-fresh" cx={activeFresh.x} cy={activeFresh.y} r={3.5} />}
      {activeFiler && <circle className="na-dot-active na-l-filers" cx={activeFiler.x} cy={activeFiler.y} r={4} />}
      {none && (
        <text className="na-empty-text" x={FRAME.left + PLOT_W / 2} y={(p.panel.top + chart.baseY) / 2} textAnchor="middle">
          no month counted yet
        </text>
      )}
      <HitSlots {...p} />
    </svg>
  );
}

// --- panel 3: the Narrative Index overlay ---------------------------------------------------------------------

function IndexPanel({ data, ...p }: PanelProps & { data: AdoptionOut }) {
  const chart = useMemo(() => layoutOverlay(data.overlay, p.axis), [data.overlay, p.axis]);
  const labels = useMemo(() => axisLabels(p.axis), [p.axis]);
  const active = useMemo(() => {
    if (p.cursor == null || !p.axis[p.cursor]) return { card: null, spy: null };
    return overlayPointAtMonth(data.overlay, p.axis, p.axis[p.cursor], data.asof, chart.domain, p.panel);
  }, [data.overlay, data.asof, p.axis, p.cursor, chart.domain, p.panel]);
  const cardTone = chart.cardEnd ? (chart.cardEnd.value > 100 ? " pos" : chart.cardEnd.value < 100 ? " neg" : "") : "";
  return (
    <svg
      className="na-svg"
      data-testid="na-panel-index"
      viewBox={`0 0 ${FRAME.w} ${p.panel.h}`}
      aria-hidden="true"
    >
      <YTicks ticks={chart.ticks} format={(v) => v.toFixed(v === 100 ? 0 : 1)} />
      <line className="na-base100" x1={FRAME.left} x2={FRAME.left + PLOT_W} y1={chart.baseline} y2={chart.baseline} />
      <Backdrop {...p} />
      {chart.spy && <path className="na-line na-l-spy" data-testid="na-spy-line" d={chart.spy} />}
      {chart.card && <path className={`na-line na-l-card${cardTone}`} data-testid="na-card-line" d={chart.card} />}
      {chart.spyEnd && <circle className="na-dot na-l-spy" cx={chart.spyEnd.x} cy={chart.spyEnd.y} r={2} />}
      {chart.cardEnd && <circle className={`na-dot na-l-card${cardTone}`} cx={chart.cardEnd.x} cy={chart.cardEnd.y} r={2.3} />}
      {active.spy && <circle className="na-dot-active na-l-spy" cx={active.spy.x} cy={active.spy.y} r={3.5} />}
      {active.card && <circle className={`na-dot-active na-l-card${cardTone}`} cx={active.card.x} cy={active.card.y} r={4} />}
      {labels.map((l) => (
        <text key={`${l.year}${l.text}`} className="na-axis" x={l.x} y={p.panel.h - 6} textAnchor={l.anchor}>
          {l.year ? l.text.slice(0, 4) : fmtMonth(l.text)}
        </text>
      ))}
      <HitSlots {...p} />
    </svg>
  );
}

// --- the curve (the three panels, one readout) -----------------------------------------------------------------

function Curve({ data }: { data: AdoptionOut }) {
  const [hover, setHover] = useState<number | null>(null);
  const [pinned, setPinned] = useState<number | null>(null);
  const [mode, setMode] = useState<BarMode>("counts");
  const [activeGroups, setActiveGroups] = useState<ReadonlySet<Group>>(() => new Set(GROUPS));

  const cursor = hover ?? pinned;
  const axis = useMemo(() => data.months.map((m) => m.month), [data.months]);
  const n = axis.length;
  const start = lastCountedIndex(data.months);
  const month = cursor != null ? data.months[cursor] : undefined;
  const lines = month ? monthReadout(month, data) : null;
  const summed = data.months.some((m) => m.terms_counted > 1);
  const ringed = data.months.some((m) => m.counted && m.filers_sampled);
  const allSampled = ringed && data.months.filter((m) => m.counted).every((m) => m.filers_sampled);
  const hasPartial = data.months.some(
    (m) => m.counted && (m.read_early || (data.counts.signal_terms > 0 && m.terms_counted < data.counts.signal_terms)),
  );
  const hasUnclosed = data.months.some((m) => !m.counted && !m.closed);

  const toggleGroup = (g: Group) => {
    setActiveGroups((prev) => {
      const next = new Set(prev);
      if (next.has(g)) {
        if (next.size === 1) return new Set(GROUPS);
        next.delete(g);
      } else {
        next.add(g);
      }
      return next;
    });
  };

  const onPin = (i: number) => {
    setPinned((prev) => (prev === i ? null : i));
  };

  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      const next = stepMonth(cursor, n, e.key === "ArrowLeft" ? -1 : 1, start);
      setHover(next);
      setPinned(null);
    } else if (e.key === "Home") {
      e.preventDefault();
      setHover(0);
      setPinned(null);
    } else if (e.key === "End") {
      e.preventDefault();
      setHover(Math.max(0, n - 1));
      setPinned(null);
    } else if (e.key === "Escape") {
      setHover(null);
      setPinned(null);
    }
  };
  const common = { axis, cursor, pinned, onHover: setHover, onPin };

  return (
    <>
      <div className={`na-readout${pinned != null ? " pinned" : ""}`} data-testid="na-readout" aria-live="polite">
        {lines ? (
          <>
            <div className="na-ro-top">
              <div className="na-ro-head">{lines[0]}</div>
              {pinned != null && (
                <button type="button" className="na-unpin" onClick={() => { setPinned(null); setHover(null); }}>
                  pinned · clear
                </button>
              )}
            </div>
            {lines.slice(1).map((l) => (
              <div key={l} className="na-ro-line">
                {l}
              </div>
            ))}
          </>
        ) : (
          <span className="na-prompt">{PROMPT}</span>
        )}
      </div>
      <div
        className="na-frame"
        data-testid="na-frame"
        role="group"
        tabIndex={0}
        aria-label={`${data.thesis_name}: documents, filers and the tape by month`}
        onKeyDown={onKey}
        onFocus={(e) => {
          if (e.target === e.currentTarget) setHover((c) => c ?? pinned ?? start);
        }}
        onMouseLeave={() => setHover(null)}
      >
        <section className="na-panel" aria-label="Documents per month">
          <header className="na-panel-head">
            <h3 className="na-panel-title">Documents per month</h3>
            <span className="na-legend">
              {GROUPS.map((g) => {
                const on = activeGroups.has(g);
                return (
                  <button
                    key={g}
                    type="button"
                    className={`na-key na-key-btn${on ? "" : " off"}`}
                    aria-pressed={on}
                    title={`${GROUP_FORMS[g]} (click to toggle)`}
                    onClick={() => toggleGroup(g)}
                  >
                    <i className={`na-swatch na-g-${g}`} />
                    {GROUP_LABEL[g]}
                  </button>
                );
              })}
              {hasPartial && (
                <span className="na-key" title="Partial month: fewer than all terms stored or counted before month close">
                  <i className="na-swatch-partial" />
                  partial
                </span>
              )}
              {hasUnclosed && (
                <span className="na-key" title="Current month: not closed at the as-of">
                  <i className="na-swatch-unclosed" />
                  open month
                </span>
              )}
            </span>
            <span className="na-mode" role="group" aria-label="Document bar scale">
              <button
                type="button"
                className={`na-mode-btn${mode === "counts" ? " on" : ""}`}
                aria-pressed={mode === "counts"}
                onClick={() => setMode("counts")}
              >
                Counts
              </button>
              <button
                type="button"
                className={`na-mode-btn${mode === "share" ? " on" : ""}`}
                aria-pressed={mode === "share"}
                onClick={() => setMode("share")}
              >
                100%
              </button>
            </span>
          </header>
          <DocumentsPanel data={data} panel={DOC_PANEL} activeGroups={activeGroups} mode={mode} {...common} />
          {summed && (
            <p className="na-note" data-testid="na-double-count">
              Summed over the card's SIGNAL terms: a filing that matches two terms counts twice here. The table below
              counts each term alone.
            </p>
          )}
        </section>
        <section className="na-panel" aria-label="Filers per month">
          <header className="na-panel-head">
            <h3 className="na-panel-title">Filers per month</h3>
            <span className="na-legend">
              <span className="na-key">
                <i className="na-swatch-line na-l-filers" />
                filers
              </span>
              <span className="na-key">
                <i className="na-swatch-line na-l-fresh" />
                new filers
              </span>
              {ringed && !allSampled && (
                <span className="na-key">
                  <i className="na-swatch-ring" />
                  at least
                </span>
              )}
            </span>
          </header>
          <FilersPanel data={data} panel={FILER_PANEL} {...common} />
          <p className="na-note">
            Distinct companies, summed over the terms. New filers: companies with no document for that term in an earlier
            stored month.
            {allSampled &&
              ' Every month reads "at least": only part of each month\'s filers were shown by EDGAR, so each count is a floor.'}
            {ringed && !allSampled && ' A ringed month reads "at least": a term showed only part of its filers.'}
          </p>
        </section>
        <section className="na-panel" aria-label="The card's basket against SPY">
          <header className="na-panel-head">
            <h3 className="na-panel-title">The tape</h3>
            <span className="na-legend">
              <span className="na-key">
                <i className="na-swatch-line na-l-card" />
                card (equal weight)
              </span>
              <span className="na-key">
                <i className="na-swatch-line na-l-spy" />
                SPY
              </span>
            </span>
          </header>
          <IndexPanel data={data} panel={INDEX_PANEL} {...common} />
          <p className="na-note">
            The card's basket as of {fmtDay(data.asof)}, looking back, against SPY: both 100 on the first session
            shown, rebalanced daily. Context and Bench names are left out.
            {data.overlay.card_reason && ` No card line: ${data.overlay.card_reason}.`}
            {data.overlay.spy_reason && ` No SPY line: ${data.overlay.spy_reason}.`}
          </p>
          <FlaggedNote flagged={data.overlay.flagged} title={flaggedRule("they are left out of the price line. The term-mention counts are not price readings and do not change.")} />
        </section>
      </div>
    </>
  );
}

// --- the term table ---------------------------------------------------------------------------------------------

function TermTh({
  sortKey,
  label,
  num,
  sort,
  onSort,
}: {
  sortKey: TermSortKey;
  label: string;
  num?: boolean;
  sort: TermSort;
  onSort: (k: TermSortKey) => void;
}) {
  const on = sort?.key === sortKey;
  return (
    <th
      className={num ? "na-num" : undefined}
      aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
    >
      <button type="button" className={`na-th-btn${on ? " on" : ""}`} onClick={() => onSort(sortKey)}>
        {label}
        {on && (
          <span className="na-arrow" aria-hidden="true">
            {sort.dir === "asc" ? " ↑" : " ↓"}
          </span>
        )}
      </button>
    </th>
  );
}

function TermTable({ data }: { data: AdoptionOut }) {
  const [sort, setSort] = useState<TermSort>(null);
  const last = data.counts.last_counted_month;
  const rows = useMemo(() => sortTerms(data.terms, sort), [data.terms, sort]);
  const max3 = useMemo(() => Math.max(0, ...data.terms.map((t) => t.last_3_documents ?? 0)), [data.terms]);
  const max12 = useMemo(() => Math.max(0, ...data.terms.map((t) => t.last_12_documents ?? 0)), [data.terms]);
  const onSort = (k: TermSortKey) => setSort((cur) => nextTermSort(cur, k));

  return (
    <section className="na-terms" aria-label="The card's SIGNAL terms">
      <h3 className="na-panel-title">SIGNAL terms</h3>
      {data.terms.length === 0 ? (
        <p className="na-note">This card has no SIGNAL terms, so no filings are counted for it.</p>
      ) : (
        <div className="na-table-wrap">
          <table className="na-table" data-testid="na-terms">
            <thead>
              <tr>
                <TermTh sortKey="term" label="Term" sort={sort} onSort={onSort} />
                <TermTh
                  sortKey="last_month"
                  label={last ? `Last month (${fmtMonth(last)})` : "Last month"}
                  num
                  sort={sort}
                  onSort={onSort}
                />
                <TermTh sortKey="last_3" label="Last 3 months" num sort={sort} onSort={onSort} />
                <TermTh sortKey="last_12" label="Last 12 months" num sort={sort} onSort={onSort} />
                <TermTh sortKey="filers" label="Filers, last month" num sort={sort} onSort={onSort} />
                <TermTh sortKey="first" label="First appeared" sort={sort} onSort={onSort} />
              </tr>
            </thead>
            <tbody>
              {rows.map((t) =>
                t.counted ? (
                  <tr
                    key={t.term}
                    data-testid="na-term"
                    title={`${plural(t.months_stored, "month", "months")} stored for "${t.term}"`}
                  >
                    <td className="na-term-name">{t.term}</td>
                    <td className="na-num">{t.last_month_documents == null ? "—" : t.last_month_documents.toLocaleString("en-US")}</td>
                    <td className="na-num na-bar-td">
                      {max3 > 0 && t.last_3_documents != null && t.last_3_documents > 0 && (
                        <span
                          className="na-cell-bar"
                          aria-hidden="true"
                          style={{ width: `${Math.max(2, Math.round((t.last_3_documents / max3) * 100))}%` }}
                        />
                      )}
                      <span className="na-cell-val">{windowWords(t.last_3_documents, t.months_last_3, 3)}</span>
                    </td>
                    <td className="na-num na-bar-td">
                      {max12 > 0 && t.last_12_documents != null && t.last_12_documents > 0 && (
                        <span
                          className="na-cell-bar"
                          aria-hidden="true"
                          style={{ width: `${Math.max(2, Math.round((t.last_12_documents / max12) * 100))}%` }}
                        />
                      )}
                      <span className="na-cell-val">{windowWords(t.last_12_documents, t.months_last_12, 12)}</span>
                    </td>
                    <td className="na-num">{filersWords(t)}</td>
                    <td>{firstWords(t)}</td>
                  </tr>
                ) : (
                  <tr key={t.term} className="quiet" data-testid="na-term">
                    <td className="na-term-name">{t.term}</td>
                    <td colSpan={5} className="na-uncounted">
                      {NOT_COUNTED}
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        </div>
      )}
      <p className="na-note">
        Each term is counted alone, so the rows do not add up to the panels above (a filing that matches two terms is in
        both). A window sums the months stored; "—" means none is stored, never zero.
      </p>
    </section>
  );
}

// --- the page ---------------------------------------------------------------------------------------------------

export default function NarrativeAdoption({ asof, onSelectThesis }: DashboardProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [query, setQuery] = useState("");

  const cardsQ = useAdoptionCards(asof);
  const cards = useMemo(() => cardsQ.data?.cards ?? [], [cardsQ.data]);
  // the picked card while it is counted at the as-of; else the first A→Z
  const thesisId = picked != null && cards.some((c) => c.thesis_id === picked) ? picked : (cards[0]?.thesis_id ?? null);
  const q = useNarrativeAdoption(asof, thesisId);
  const data = q.data;

  // the cards whose name holds the query, and the dropdown's options: those, with the picked card kept on screen
  const hits = useMemo(() => filterCards(cards, query), [cards, query]);
  const options = useMemo(() => {
    const current = cards.find((c) => c.thesis_id === thesisId);
    return current && !hits.includes(current) ? [current, ...hits] : hits;
  }, [cards, hits, thesisId]);

  if (cardsQ.isLoading) return <div className="dash-note">Loading the cards…</div>;
  if (cardsQ.error || !cardsQ.data) return <div className="dash-note">Could not load the Narrative Adoption's cards.</div>;
  if (cards.length === 0) return <div className="dash-note">No card existed by this as-of.</div>;

  const stale = data != null && (data.thesis_id !== thesisId || data.asof !== asof);

  return (
    <div className="na">
      <div className="na-controls">
        <div className="na-pick">
          <span className="na-seg-label">Card</span>
          <input
            type="search"
            className="na-search"
            aria-label="Find a card"
            placeholder="Find a card"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select className="na-select" aria-label="Card" value={thesisId ?? ""} onChange={(e) => setPicked(e.target.value)}>
            {options.map((c) => (
              <option key={c.thesis_id} value={c.thesis_id}>
                {c.name} · {plural(c.links, "link", "links")}
              </option>
            ))}
          </select>
          {query.trim() && (
            <span className="na-n">
              {plural(hits.length, "match", "matches")} of {cards.length}
            </span>
          )}
        </div>
        {q.isFetching && data && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      {q.isLoading || (!data && q.isFetching) ? (
        <div className="dash-note">{SLOW}</div>
      ) : q.error || !data ? (
        <div className="dash-note">Could not load the Narrative Adoption.</div>
      ) : (
        <>
          <div className="na-title">
            <button
              type="button"
              className="na-card-btn"
              title={`Open ${data.thesis_name} in the Cockpit`}
              onClick={() => onSelectThesis(data.thesis_id)}
            >
              {data.thesis_name}
            </button>
            <span className="na-n">
              {plural(data.counts.signal_terms, "SIGNAL term", "SIGNAL terms")} · {data.counts.months_counted} of{" "}
              {data.counts.months_total} months stored
            </span>
          </div>
          {data.counts.signal_terms === 0 && (
            <div className="dash-note">This card has no SIGNAL terms, so no filings are counted for it.</div>
          )}
          <Curve key={`${data.thesis_id}|${data.asof}`} data={data} />
          <TermTable data={data} />
          <div className="dash-note na-work" data-testid="na-work">
            {workLine(data)}
          </div>
          {data.roster_fallback && (
            <div className="dash-note na-work">
              Today's roster stands in for this card's line: rosters are point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
            </div>
          )}
          <ul className="na-limits">
            <li>
              Counts are documents matching a term's full-text query in SEC's own index, per calendar month of the filing
              date, from 2001 on. An exhibit and its filing may both count, and a term in a boilerplate risk factor counts
              like one in an MD&amp;A paragraph.
            </li>
            <li>
              Only the card's SIGNAL terms are counted, as the card's term set reads today: a term set has no history, so
              the same terms are used at every as-of, and a term edited since is "{NOT_COUNTED}" until the next weekly
              run. Months stored under a term no longer in the set stay stored and are not drawn.
            </li>
            <li>
              A month is blank when no count is stored for it: the as-of's own month (it is stored once it closes), a month
              that failed to read or was deferred, or a term added since the last weekly run. A blank is never a zero. A
              month read before it closed is partial until the next weekly run re-reads it.
            </li>
            <li>
              Filers read "at least" where a term-month matched more documents than EDGAR lists on one page and its filer
              list was not complete; new filers are counted from the filers seen, so they are approximate there too.
            </li>
          </ul>
        </>
      )}
    </div>
  );
}
