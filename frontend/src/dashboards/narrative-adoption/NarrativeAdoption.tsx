import { useMemo, useState, type KeyboardEvent } from "react";

import { FlaggedNote } from "../FlaggedNote";
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
  plotHeight,
  slotWidth,
  slotX,
  stepMonth,
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
  plural,
  windowWords,
  workLine,
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
  "Hover a month for its counts: the form groups, the terms with the most documents, the filers and the tape. The left and right arrow keys step through the months, Home and End jump to the first and last, Escape clears.";

const compact = (v: number): string => (v >= 1000 ? `${Number((v / 1000).toFixed(1))}k` : String(Number(v.toFixed(1))));

// --- shared parts of the three panels ------------------------------------------------------------------------

type PanelProps = {
  axis: readonly string[];
  panel: Panel;
  cursor: number | null;
  onHover: (i: number) => void;
};

/** The column behind the month under the cursor, the January rules, and the transparent hit slots. */
function Backdrop({ axis, panel, cursor }: Pick<PanelProps, "axis" | "panel" | "cursor">) {
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
        <rect
          className="na-cursor"
          data-testid="na-cursor"
          x={slotX(cursor, n)}
          y={panel.top}
          width={slotWidth(n)}
          height={plotHeight(panel)}
        />
      )}
    </>
  );
}

function HitSlots({ axis, panel, onHover }: Pick<PanelProps, "axis" | "panel" | "onHover">) {
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

function DocumentsPanel({ data, ...p }: PanelProps & { data: AdoptionOut }) {
  const chart = useMemo(() => layoutBars(data.months), [data.months]);
  const none = data.counts.months_counted === 0;
  return (
    <svg
      className="na-svg"
      data-testid="na-panel-docs"
      viewBox={`0 0 ${FRAME.w} ${p.panel.h}`}
      aria-hidden="true"
    >
      <YTicks ticks={chart.ticks} />
      <Backdrop {...p} />
      {chart.bars.map((b) => (
        <g key={b.index} data-testid="na-bar" data-index={b.index}>
          {b.zero ? (
            <rect className="na-zero" data-testid="na-zero" x={b.x} y={chart.baseY - 1.5} width={b.w} height={1.5} />
          ) : (
            b.segments.map((s) => (
              <rect key={s.group} className={`na-seg na-g-${s.group}`} x={b.x} y={s.y} width={b.w} height={s.h} />
            ))
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
        <circle key={`n${pt.index}`} className="na-dot na-l-fresh" cx={pt.x} cy={pt.y} r={1.5} />
      ))}
      {chart.filers.points.map((pt) => (
        <g key={`f${pt.index}`}>
          <circle className="na-dot na-l-filers" cx={pt.x} cy={pt.y} r={1.9} />
          {pt.ring && <circle className="na-ring" data-testid="na-ring" cx={pt.x} cy={pt.y} r={3.6} />}
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

// --- panel 3: the Narrative Index overlay ---------------------------------------------------------------------

function IndexPanel({ data, ...p }: PanelProps & { data: AdoptionOut }) {
  const chart = useMemo(() => layoutOverlay(data.overlay, p.axis), [data.overlay, p.axis]);
  const labels = useMemo(() => axisLabels(p.axis), [p.axis]);
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
      {chart.card && <path className="na-line na-l-card" data-testid="na-card-line" d={chart.card} />}
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
  const [cursor, setCursor] = useState<number | null>(null);
  const axis = useMemo(() => data.months.map((m) => m.month), [data.months]);
  const n = axis.length;
  const start = lastCountedIndex(data.months);
  const month = cursor != null ? data.months[cursor] : undefined;
  const lines = month ? monthReadout(month, data) : null;
  const summed = data.months.some((m) => m.terms_counted > 1);
  const ringed = data.months.some((m) => m.counted && m.filers_sampled);
  const allSampled = ringed && data.months.filter((m) => m.counted).every((m) => m.filers_sampled);

  const onKey = (e: KeyboardEvent<HTMLElement>) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      setCursor((c) => stepMonth(c, n, e.key === "ArrowLeft" ? -1 : 1, start));
    } else if (e.key === "Home") {
      e.preventDefault();
      setCursor(0);
    } else if (e.key === "End") {
      e.preventDefault();
      setCursor(Math.max(0, n - 1));
    } else if (e.key === "Escape") {
      setCursor(null);
    }
  };
  const common = { axis, cursor, onHover: setCursor };

  return (
    <>
      <div className="na-readout" data-testid="na-readout" aria-live="polite">
        {lines ? (
          lines.map((l, i) => (
            <div key={l} className={i === 0 ? "na-ro-head" : "na-ro-line"}>
              {l}
            </div>
          ))
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
          if (e.target === e.currentTarget) setCursor((c) => c ?? start);
        }}
        onMouseLeave={() => setCursor(null)}
      >
        <section className="na-panel" aria-label="Documents per month">
          <header className="na-panel-head">
            <h3 className="na-panel-title">Documents per month</h3>
            <span className="na-legend">
              {GROUPS.map((g) => (
                <span key={g} className="na-key" title={GROUP_FORMS[g]}>
                  <i className={`na-swatch na-g-${g}`} />
                  {GROUP_LABEL[g]}
                </span>
              ))}
            </span>
          </header>
          <DocumentsPanel data={data} panel={DOC_PANEL} {...common} />
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
          <FlaggedNote flagged={data.overlay.flagged} />
        </section>
      </div>
    </>
  );
}

// --- the term table ---------------------------------------------------------------------------------------------

function TermTable({ data }: { data: AdoptionOut }) {
  const last = data.counts.last_counted_month;
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
                <th>Term</th>
                <th className="na-num">{last ? `Last month (${fmtMonth(last)})` : "Last month"}</th>
                <th className="na-num">Last 3 months</th>
                <th className="na-num">Last 12 months</th>
                <th className="na-num">Filers, last month</th>
                <th>First appeared</th>
              </tr>
            </thead>
            <tbody>
              {data.terms.map((t) =>
                t.counted ? (
                  <tr key={t.term} data-testid="na-term">
                    <td className="na-term-name">{t.term}</td>
                    <td className="na-num">{t.last_month_documents == null ? "—" : t.last_month_documents.toLocaleString("en-US")}</td>
                    <td className="na-num">{windowWords(t.last_3_documents, t.months_last_3, 3)}</td>
                    <td className="na-num">{windowWords(t.last_12_documents, t.months_last_12, 12)}</td>
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
