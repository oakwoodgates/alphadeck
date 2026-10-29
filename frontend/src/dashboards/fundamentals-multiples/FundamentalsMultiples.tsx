import { useMemo, useRef, useState, type KeyboardEvent } from "react";

import type { DashboardProps } from "../registry";
import "./fundamentals-multiples.css";
import {
  BASE_Y,
  CELL_W,
  PAD_X,
  YOY_CAP,
  groupsFor,
  hasMedian,
  layoutMedian,
  layoutName,
  medianKey,
  moveFocus,
  nameKey,
  stepItem,
  type Dir,
  type Group,
  type MedianChart,
  type NameChart,
} from "./multiplesLayout";
import {
  barTitle,
  barWords,
  companyTitle,
  compactUsd,
  emptyWords,
  filingLine,
  filterCards,
  fmtDay,
  lastPrintWords,
  lastYoyWords,
  plural,
  pointWords,
  quarterWords,
  setAsideWords,
  workLine,
} from "./multiplesView";
import {
  useFundamentalsMultiples,
  useMultiplesCards,
  type MultiplesCompanyOut,
  type MultiplesMedianOut,
  type MultiplesOut,
  type MultiplesRowOut,
} from "./useFundamentalsMultiples";

// Fundamentals Multiples (docs/dashboards/fundamentals-multiples.md): ONE card's revenue series, name by name and
// link by link. Each name is a small chart of its last quarterly revenue prints (a bar per print, placed by its
// period end and dated by the filing that made it public) with the change on the year before as dots above the
// bars; each link and the card carry the median of those changes. Plain inline SVG laid out by the pure
// multiplesLayout.ts; no chart library. Display only. Every number (the window, the dots, the medians, the counts)
// is computed server-side; this page picks the card and the toggle, lays the charts out and words them.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";
const SLOW = "Reading this card's names — the first load after a run or a basket edit can take a moment.";
const PROMPT =
  "Hover a bar for the filing behind it: its period end, value, basis, filing date and accession. The arrow keys move between charts, Shift with the left and right arrows steps through one chart's bars, and Enter opens a name in the Cockpit.";
const ARROWS: Record<string, Dir> = { ArrowUp: "up", ArrowDown: "down", ArrowLeft: "left", ArrowRight: "right" };
const CARD_KEY = "card";

type Cursor = { key: string; item: number | null };

type NameCellInfo = {
  kind: "name";
  key: string;
  row: MultiplesRowOut;
  company: MultiplesCompanyOut;
  chart: NameChart;
};
type MedianCellInfo = {
  kind: "median";
  key: string;
  /** What the median is over: the card, or the row's label. */
  of: string;
  median: MultiplesMedianOut;
  chart: MedianChart;
};
type CellInfo = NameCellInfo | MedianCellInfo;

const rowTag = (r: MultiplesRowOut): string => {
  switch (r.kind) {
    case "old_link":
      return "not a link today";
    case "no_link":
      return "rows with no link";
    case "silent":
      return "not in the call · never in a median";
    default:
      return r.descriptor ?? "";
  }
};

// --- the readout -----------------------------------------------------------------------------------------------

function Readout({ cell, item, data }: { cell: CellInfo | null; item: number | null; data: MultiplesOut }) {
  if (!cell) {
    return (
      <div className="fm-readout" data-testid="fm-readout" aria-live="polite">
        <span className="fm-prompt">{PROMPT}</span>
      </div>
    );
  }
  if (cell.kind === "median") {
    const point = item != null ? cell.chart.points[item] : undefined;
    const p = point ? cell.median.points[point.index] : undefined;
    return (
      <div className="fm-readout" data-testid="fm-readout" aria-live="polite">
        <div className="fm-ro-head">
          <span className="fm-ro-tk">{cell.of}</span>
          <span className="fm-n">
            median year-over-year change · {plural(cell.median.counting, "name", "names")} · equal weight, no score
          </span>
        </div>
        <div className="fm-ro-line">
          {p
            ? pointWords(p, cell.median.counting)
            : "No quarter has a median here: it is drawn only where two or more names have a change that quarter."}
        </div>
      </div>
    );
  }
  const { company, row } = cell;
  const placed = item != null ? cell.chart.bars.find((b) => b.index === item) : undefined;
  const bar = placed?.bar;
  return (
    <div className="fm-readout" data-testid="fm-readout" aria-live="polite">
      <div className="fm-ro-head">
        <span className="fm-ro-tk">{company.ticker}</span>
        {company.name && <span className="fm-ro-name">{company.name}</span>}
        <span className="fm-n">{row.label}</span>
        {company.state === "thin" && <span className="fm-n">thin: fewer than {data.thin_below} quarters</span>}
      </div>
      {bar ? (
        <>
          <div className="fm-ro-line">{barWords(bar)}</div>
          <div className="fm-ro-line">
            {filingLine(bar)}
            {bar.url && (
              <>
                {" · "}
                <a className="fm-src" href={bar.url} target="_blank" rel="noreferrer">
                  filing on EDGAR
                </a>
              </>
            )}
          </div>
        </>
      ) : (
        <div className="fm-ro-line">{emptyWords(company) || "no quarter in view"}</div>
      )}
      {company.last_print && (
        <div className="fm-ro-line">
          last print: {lastPrintWords(company.last_print, data.asof)} · {lastYoyWords(company.last_print)}
        </div>
      )}
      {company.set_aside > 0 && <div className="fm-ro-line">{setAsideWords(company.set_aside)} (a data-quality set-aside)</div>}
    </div>
  );
}

// --- the charts ------------------------------------------------------------------------------------------------

/** One hatched fill, defined once for every chart on the page (a derived quarter is hatched). */
function Defs() {
  return (
    <svg className="fm-defs" width="0" height="0" aria-hidden="true" focusable="false">
      <defs>
        <pattern id="fm-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <line className="fm-hatch-line" x1="0" y1="0" x2="0" y2="4" />
        </pattern>
      </defs>
    </svg>
  );
}

function AxisLabels({ labels }: { labels: NameChart["labels"] }) {
  return (
    <>
      {labels.map((l) => (
        <text key={l.anchor} className="fm-axis" x={l.x} y={l.y} textAnchor={l.anchor}>
          {quarterWords(l.text)}
        </text>
      ))}
    </>
  );
}

function NameSvg({
  company,
  chart,
  selected,
  onHover,
}: {
  company: MultiplesCompanyOut;
  chart: NameChart;
  selected: number | null;
  onHover: (item: number) => void;
}) {
  const empty = chart.bars.length === 0;
  return (
    <svg
      className="fm-svg"
      width={chart.width}
      height={chart.height}
      viewBox={`0 0 ${chart.width} ${chart.height}`}
      aria-hidden="true"
    >
      {empty ? (
        <>
          <rect className="fm-empty-box" x={PAD_X} y={6} width={CELL_W - 2 * PAD_X} height={BASE_Y - 6} rx={4} />
          <text className="fm-empty-text" x={CELL_W / 2} y={(BASE_Y + 6) / 2 + 3} textAnchor="middle">
            {emptyWords(company)}
          </text>
        </>
      ) : (
        <>
          <line className="fm-zero" x1={PAD_X} x2={CELL_W - PAD_X} y1={chart.zeroY} y2={chart.zeroY} />
          <line className="fm-base" x1={PAD_X} x2={CELL_W - PAD_X} y1={chart.baseY} y2={chart.baseY} />
          {chart.bars.map((b) => (
            <g
              key={b.index}
              className={`fm-bar-g${selected === b.index ? " sel" : ""}`}
              data-testid="fm-bar"
              data-index={b.index}
              onMouseEnter={() => onHover(b.index)}
            >
              <title>{barTitle(b.bar)}</title>
              <rect className="fm-hit" x={b.hit.x} y={0} width={b.hit.w} height={chart.height} />
              <rect className={`fm-bar${b.derived ? " derived" : ""}`} x={b.x} y={b.y} width={b.w} height={b.h} />
              {b.dot && (
                <circle
                  className={`fm-dot ${b.dot.tone}${b.dot.clipped ? " clipped" : ""}`}
                  data-testid="fm-dot"
                  cx={b.dot.cx}
                  cy={b.dot.cy}
                  r={2.4}
                />
              )}
            </g>
          ))}
          {chart.top && (
            <text className="fm-top" x={chart.top.x} y={chart.top.y}>
              {compactUsd(chart.top.value)}
            </text>
          )}
        </>
      )}
      <AxisLabels labels={chart.labels} />
    </svg>
  );
}

function MedianSvg({
  median,
  chart,
  selected,
  onHover,
}: {
  median: MultiplesMedianOut;
  chart: MedianChart;
  selected: number | null;
  onHover: (item: number) => void;
}) {
  return (
    <svg
      className="fm-svg"
      width={chart.width}
      height={chart.height}
      viewBox={`0 0 ${chart.width} ${chart.height}`}
      aria-hidden="true"
    >
      <line className="fm-zero" x1={PAD_X} x2={CELL_W - PAD_X} y1={chart.zeroY} y2={chart.zeroY} />
      <text className="fm-axis" x={CELL_W - PAD_X} y={chart.topY - 3} textAnchor="end">
        +{YOY_CAP}%
      </text>
      <text className="fm-axis" x={CELL_W - PAD_X} y={chart.zeroY - 3} textAnchor="end">
        0
      </text>
      <text className="fm-axis" x={CELL_W - PAD_X} y={chart.bottomY + 9} textAnchor="end">
        -{YOY_CAP}%
      </text>
      {chart.paths.map((d) => (
        <path key={d} className="fm-line" d={d} />
      ))}
      {chart.points.map((p, i) => (
        <g
          key={p.index}
          className={`fm-pt-g${selected === i ? " sel" : ""}`}
          data-testid="fm-point"
          data-index={i}
          onMouseEnter={() => onHover(i)}
        >
          <title>{pointWords(median.points[p.index], median.counting)}</title>
          <circle className="fm-hit-dot" cx={p.cx} cy={p.cy} r={7} />
          <circle className={`fm-dot ${p.tone}${p.clipped ? " clipped" : ""}`} cx={p.cx} cy={p.cy} r={2.6} />
        </g>
      ))}
      <AxisLabels labels={chart.labels} />
    </svg>
  );
}

// --- the body --------------------------------------------------------------------------------------------------

function Body({
  data,
  showSilent,
  onSelectThesis,
}: {
  data: MultiplesOut;
  showSilent: boolean;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [active, setActive] = useState<string | null>(null);
  const [shown, setShown] = useState<Cursor | null>(null);

  const byCompany = useMemo(() => new Map(data.companies.map((c) => [c.company_key, c])), [data]);
  // every cell on the page, whatever the toggle says (the toggle only decides which are drawn)
  const cells = useMemo(() => {
    const out = new Map<string, CellInfo>();
    out.set(medianKey(CARD_KEY), {
      kind: "median",
      key: medianKey(CARD_KEY),
      of: data.thesis_name,
      median: data.card_line,
      chart: layoutMedian(data.card_line, data.axis),
    });
    for (const row of data.rows) {
      if (row.median && hasMedian(row)) {
        out.set(medianKey(row.key), {
          kind: "median",
          key: medianKey(row.key),
          of: row.label,
          median: row.median,
          chart: layoutMedian(row.median, data.axis),
        });
      }
      for (const ck of row.companies) {
        const company = byCompany.get(ck);
        if (company) {
          out.set(nameKey(row.key, ck), {
            kind: "name",
            key: nameKey(row.key, ck),
            row,
            company,
            chart: layoutName(company, data.axis),
          });
        }
      }
    }
    return out;
  }, [data, byCompany]);
  const groups: Group[] = useMemo(() => groupsFor(data.rows, showSilent), [data.rows, showSilent]);
  const keys = useMemo(() => groups.flatMap((g) => g.keys), [groups]);
  const tab = active != null && keys.includes(active) ? active : (keys[0] ?? null);
  // a chart the toggle has hidden since it was looked at leaves the readout
  const cell = shown != null && keys.includes(shown.key) ? (cells.get(shown.key) ?? null) : null;

  const itemCount = (c: CellInfo): number => (c.kind === "name" ? c.company.bars.length : c.chart.points.length);
  const focusCell = (key: string) => {
    const el = Array.from(frame.current?.querySelectorAll<HTMLElement>("[data-key]") ?? []).find(
      (e) => e.getAttribute("data-key") === key,
    );
    el?.focus();
  };
  const onCellKey = (e: KeyboardEvent<HTMLElement>, c: CellInfo) => {
    const dir = ARROWS[e.key];
    if (dir) {
      e.preventDefault();
      if (e.shiftKey && (dir === "left" || dir === "right")) {
        const current = shown?.key === c.key ? shown.item : null;
        setShown({ key: c.key, item: stepItem(current, itemCount(c), dir === "left" ? -1 : 1) });
      } else {
        const next = moveFocus(groups, c.key, dir);
        setActive(next);
        focusCell(next);
      }
    } else if ((e.key === "Enter" || e.key === " ") && c.kind === "name") {
      e.preventDefault();
      onSelectThesis(data.thesis_id, c.company.ticker);
    } else if (e.key === "Escape") {
      setShown(null);
    }
  };
  const show = (c: CellInfo, item: number | null) => setShown({ key: c.key, item });
  // the readout stays on the last bar looked at (so its filing link can be reached), until another is chosen
  const onFocusCell = (c: CellInfo) => {
    setActive(c.key);
    const n = itemCount(c);
    setShown((cur) => (cur?.key === c.key ? cur : { key: c.key, item: n > 0 ? n - 1 : null }));
  };

  const medianCell = (c: MedianCellInfo, label: string, sub: string) => {
    const sel = shown?.key === c.key ? shown.item : null;
    return (
      <div
        key={c.key}
        className="fm-cell median"
        role="group"
        tabIndex={c.key === tab ? 0 : -1}
        aria-label={`${label}: ${sub}`}
        data-key={c.key}
        data-testid="fm-cell"
        onFocus={() => onFocusCell(c)}
        onKeyDown={(e) => onCellKey(e, c)}
      >
        <div className="fm-cell-head">
          <span className="fm-cell-title">{label}</span>
          <span className="fm-n">{sub}</span>
        </div>
        <MedianSvg median={c.median} chart={c.chart} selected={sel} onHover={(i) => show(c, i)} />
        <div className="fm-cell-foot fm-n">
          {c.chart.points.length > 0
            ? `${plural(c.chart.points.length, "quarter", "quarters")} drawn`
            : "no quarter has a median"}
        </div>
      </div>
    );
  };
  const nameCell = (c: NameCellInfo) => {
    const co = c.company;
    const sel = shown?.key === c.key ? shown.item : null;
    return (
      <div
        key={c.key}
        className={`fm-cell name ${co.state}`}
        role="group"
        tabIndex={c.key === tab ? 0 : -1}
        aria-label={companyTitle(co, data.asof)}
        data-key={c.key}
        data-testid="fm-cell"
        onFocus={() => onFocusCell(c)}
        onKeyDown={(e) => onCellKey(e, c)}
      >
        <div className="fm-cell-head">
          <button
            type="button"
            className="fm-chip"
            tabIndex={-1}
            title={`Open ${co.ticker} in the Cockpit`}
            onClick={() => onSelectThesis(data.thesis_id, co.ticker)}
          >
            {co.ticker}
          </button>
          {co.name && (
            <span className="fm-cell-name" title={co.name}>
              {co.name}
            </span>
          )}
          {co.state === "thin" && (
            <span className="fm-tag" title={`fewer than ${data.thin_below} quarters`}>
              thin
            </span>
          )}
        </div>
        <NameSvg company={co} chart={c.chart} selected={sel} onHover={(i) => show(c, i)} />
        <div className="fm-cell-foot">
          {co.last_print && (
            <>
              <div className="fm-last" data-testid="fm-last">
                {lastPrintWords(co.last_print, data.asof)}
              </div>
              <div className="fm-n">{lastYoyWords(co.last_print)}</div>
            </>
          )}
          {co.set_aside > 0 && <div className="fm-n fm-aside">{setAsideWords(co.set_aside)}</div>}
        </div>
      </div>
    );
  };

  const cardMedian = cells.get(medianKey(CARD_KEY)) as MedianCellInfo;
  const visible = data.rows.filter((r) => showSilent || r.kind !== "silent");
  return (
    <>
      <Defs />
      <Readout cell={cell} item={shown?.item ?? null} data={data} />
      <div ref={frame} className="fm-frame" data-testid="fm-frame">
        <section className="fm-row fm-card" data-testid="fm-card-line" aria-label="The card line">
          <div className="fm-grid">
            {medianCell(
              cardMedian,
              `${data.thesis_name}`,
              `median of ${plural(data.card_line.counting, "name", "names")}`,
            )}
            <p className="fm-card-note">
              The card line: the median year-over-year change of the card's counting names, each name once. Drawn where
              two or more names have a change that quarter (a card with one counting name shows that name's own).
            </p>
          </div>
        </section>
        {visible.map((row) => {
          const m = cells.get(medianKey(row.key));
          return (
            <section
              key={row.key}
              className={`fm-row ${row.kind}`}
              data-testid="fm-row"
              data-key={row.key}
              aria-label={row.label}
            >
              <header className="fm-row-head">
                <h3 className="fm-row-title">{row.label}</h3>
                {rowTag(row) && <span className="fm-row-tag">{rowTag(row)}</span>}
                <span className="fm-n">{plural(row.companies.length, "name", "names")}</span>
              </header>
              {row.companies.length === 0 ? (
                <p className="fm-n fm-nobody">Nobody sits in this link on the roster at this as-of.</p>
              ) : (
                <div className="fm-grid">
                  {m && m.kind === "median" && medianCell(m, "Link median", `median of ${plural(row.companies.length, "name", "names")}`)}
                  {row.companies.map((ck) => {
                    const c = cells.get(nameKey(row.key, ck));
                    return c && c.kind === "name" ? nameCell(c) : null;
                  })}
                </div>
              )}
            </section>
          );
        })}
      </div>
    </>
  );
}

// --- the page --------------------------------------------------------------------------------------------------

export default function FundamentalsMultiples({ asof, onSelectThesis }: DashboardProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [showSilent, setShowSilent] = useState(false);

  const cardsQ = useMultiplesCards(asof);
  const cards = useMemo(() => cardsQ.data?.cards ?? [], [cardsQ.data]);
  // the picked card while it is counted at the as-of; else the first A→Z
  const thesisId =
    picked != null && cards.some((c) => c.thesis_id === picked) ? picked : (cards[0]?.thesis_id ?? null);
  const q = useFundamentalsMultiples(asof, thesisId);
  const data = q.data;

  const options = useMemo(() => {
    const hits = filterCards(cards, query);
    const current = cards.find((c) => c.thesis_id === thesisId);
    return current && !hits.includes(current) ? [current, ...hits] : hits;
  }, [cards, query, thesisId]);

  if (cardsQ.isLoading) return <div className="dash-note">Loading the cards…</div>;
  if (cardsQ.error || !cardsQ.data) return <div className="dash-note">Could not load the Fundamentals Multiples' cards.</div>;
  if (cards.length === 0) return <div className="dash-note">No card existed by this as-of.</div>;

  const stale = data != null && (data.thesis_id !== thesisId || data.asof !== asof);
  const hidden = data?.counts.silent_companies ?? 0;

  return (
    <div className="fm">
      <div className="fm-controls">
        <div className="fm-pick">
          <span className="fm-seg-label">Card</span>
          <input
            type="search"
            className="fm-search"
            aria-label="Find a card"
            placeholder="Find a card"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select
            className="fm-select"
            aria-label="Card"
            value={thesisId ?? ""}
            onChange={(e) => setPicked(e.target.value)}
          >
            {options.map((c) => (
              <option key={c.thesis_id} value={c.thesis_id}>
                {c.name} · {plural(c.links, "link", "links")}
              </option>
            ))}
          </select>
          {query.trim() && (
            <span className="fm-n">
              {plural(options.length, "match", "matches")} of {cards.length}
            </span>
          )}
        </div>
        {hidden > 0 && (
          <label
            className="fm-check"
            title="Context and Bench names never count toward the call and are never in a median; their charts are only drawn for reference"
          >
            <input type="checkbox" checked={showSilent} onChange={(e) => setShowSilent(e.target.checked)} />
            Show Context and Bench ({hidden.toLocaleString("en-US")})
          </label>
        )}
        {q.isFetching && data && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      {q.isLoading || (!data && q.isFetching) ? (
        <div className="dash-note">{SLOW}</div>
      ) : q.error || !data ? (
        <div className="dash-note">Could not load the Fundamentals Multiples.</div>
      ) : (
        <>
          <div className="fm-title">
            <button
              type="button"
              className="fm-card-btn"
              title={`Open ${data.thesis_name} in the Cockpit`}
              onClick={() => onSelectThesis(data.thesis_id)}
            >
              {data.thesis_name}
            </button>
            <span className="fm-n">
              {plural(data.counts.links_today, "link", "links")} today · {plural(data.counts.companies, "name", "names")}
            </span>
          </div>
          <Body key={data.thesis_id} data={data} showSilent={showSilent} onSelectThesis={onSelectThesis} />
          <div className="dash-note fm-work">{workLine(data)}</div>
          {data.roster_fallback && (
            <div className="dash-note fm-work">
              Today's roster stands in for this card: rosters are point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
            </div>
          )}
          <div className="dash-note fm-legend">
            Bars: quarterly revenue on each name's own scale (the top value is written above the bars); a hatched bar is
            derived. Dots: the change against the same period a year earlier on one fixed scale, {`-${YOY_CAP}%`} to{" "}
            {`+${YOY_CAP}%`} (a ring is off the scale), toned by sign. The line above each link is the median of its
            names' dots.
          </div>
          <ul className="fm-limits">
            <li>
              Revenue is the XBRL series only (companyfacts), quarterly, Q4 derived from the annual where no quarterly
              fact exists.
            </li>
            <li>
              Placed by period end, dated by filing: a print filed after the as-of is not drawn. The axis is the
              calendar quarter of the period end; fiscal calendars differ, so two names' Q2 can end in different months
              (the hover says the period end).
            </li>
            <li>
              Medians, equal weight, no score. A name with fewer than {data.thin_below} quarters is drawn and marked thin.
              The roster is the card's roster as of {fmtDay(data.asof)}; links and their descriptors are today's. Revenue
              only today: the series is rows, not columns, so margins or cash flow can slot in later as more rows.
            </li>
          </ul>
        </>
      )}
    </div>
  );
}
