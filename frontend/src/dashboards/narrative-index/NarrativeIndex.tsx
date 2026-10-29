import { useMemo, useState, type MouseEvent } from "react";

import { STATE_CLASS, STATE_LABEL } from "../../util/format";
import type { DashboardProps } from "../registry";
import "./narrative-index.css";
import {
  DEFAULT_SORT,
  SORT_OPTIONS,
  WINDOW_OPTIONS,
  createdIndex,
  fmtDay,
  fmtPct,
  linePath,
  nextSort,
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

/** The numbers above a chart. */
function Numbers({ c, window }: { c: NarrativeCardOut; window: IndexWindow }) {
  return (
    <div className="ni-nums">
      <span title="the index return over the window">
        {windowLabel(window)} {fmtPct(c.ret)}
      </span>
      <span title="the window return minus SPY's over the same sessions">vs SPY {fmtPct(c.vs_spy)}</span>
      <span
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
}: {
  c: NarrativeCardOut;
  dates: readonly string[];
  bench: readonly NarrativeBenchmarkOut[];
}) {
  const spy = benchLine(bench, "SPY");
  const iwm = benchLine(bench, "IWM");
  const domain = yDomain([c.points, spy, iwm]);
  const at = createdIndex(dates, c.created_base);
  const base = yAt(100, domain, MINI_H);
  return (
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
      {c.points.length > 1 && <path className="ni-line" d={linePath(c.points, domain, MINI_W, MINI_H)} />}
    </svg>
  );
}

// the enlarged chart's frame: the plot area inside the axis labels
const BIG = { w: 960, h: 240, left: 46, right: 8, top: 8, bottom: 20 };
const STRIP_H = 36;

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
  const [hover, setHover] = useState<number | null>(null);
  const spy = benchLine(bench, "SPY");
  const iwm = benchLine(bench, "IWM");
  const domain: Domain = yDomain([c.points, spy, iwm]);
  const pw = BIG.w - BIG.left - BIG.right;
  const ph = BIG.h - BIG.top - BIG.bottom;
  const n = dates.length;
  const x = (i: number) => BIG.left + xAt(i, n, pw);
  const y = (v: number) => BIG.top + yAt(v, domain, ph);
  const at = createdIndex(dates, c.created_base);
  const i = hover ?? n - 1;
  const maxNames = Math.max(1, c.names, ...(contributors ?? [0]));

  const onMove = (e: MouseEvent<SVGRectElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    if (r.width <= 0 || n === 0) return;
    const f = (e.clientX - r.left) / r.width;
    setHover(Math.min(n - 1, Math.max(0, Math.round(f * (n - 1)))));
  };

  return (
    <div className="ni-big-chart">
      <div className="ni-readout">
        {n > 0 ? (
          <>
            <span>{fmtDay(dates[i])}</span>
            <span className="ni-key idx">index {c.points[i]?.toFixed(1) ?? "—"}</span>
            {spy && <span className="ni-key spy">SPY {spy[i]?.toFixed(1) ?? "—"}</span>}
            {iwm && <span className="ni-key iwm">IWM {iwm[i]?.toFixed(1) ?? "—"}</span>}
            {contributors && (
              <span>
                {contributors[i] ?? 0} of {c.names} names contributing
              </span>
            )}
          </>
        ) : (
          <span>no sessions in this window</span>
        )}
      </div>
      <svg className="ni-big" viewBox={`0 0 ${BIG.w} ${BIG.h}`} role="img" aria-label={`${c.thesis_name} enlarged`}>
        {[domain[0], 100, domain[1]].map((v) => (
          <g key={v}>
            <line className={v === 100 ? "ni-base" : "ni-gridline"} x1={BIG.left} x2={BIG.w - BIG.right} y1={y(v)} y2={y(v)} />
            <text className="ni-axis" x={BIG.left - 6} y={y(v) + 3} textAnchor="end">
              {v.toFixed(v === 100 ? 0 : 1)}
            </text>
          </g>
        ))}
        {n > 0 && (
          <>
            <text className="ni-axis" x={BIG.left} y={BIG.h - 5} textAnchor="start">
              {fmtDay(dates[0])}
            </text>
            <text className="ni-axis" x={BIG.w - BIG.right} y={BIG.h - 5} textAnchor="end">
              {fmtDay(dates[n - 1])}
            </text>
          </>
        )}
        {spy && <path className="ni-bench spy" d={linePath(spy, domain, pw, ph, BIG.left, BIG.top)} />}
        {iwm && <path className="ni-bench iwm" d={linePath(iwm, domain, pw, ph, BIG.left, BIG.top)} />}
        {at != null && (
          <g data-testid="ni-created-big">
            <line className="ni-created" x1={x(at)} x2={x(at)} y1={BIG.top} y2={BIG.top + ph} />
            <text
              className="ni-axis ni-created-label"
              x={at > (n - 1) / 2 ? x(at) - 4 : x(at) + 4}
              y={BIG.top + 10}
              textAnchor={at > (n - 1) / 2 ? "end" : "start"}
            >
              created {fmtDay(c.created)}
            </text>
          </g>
        )}
        {c.points.length > 1 && (
          <path className="ni-line" d={linePath(c.points, domain, pw, ph, BIG.left, BIG.top)} />
        )}
        {hover != null && <line className="ni-hover" x1={x(hover)} x2={x(hover)} y1={BIG.top} y2={BIG.top + ph} />}
        <rect
          className="ni-hit"
          x={BIG.left}
          y={BIG.top}
          width={pw}
          height={ph}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
        />
      </svg>
      {contributors && contributors.length === n && n > 0 && (
        <svg className="ni-strip" viewBox={`0 0 ${BIG.w} ${STRIP_H}`} role="img" aria-label="names contributing each session">
          {contributors.map((k, j) => {
            const h = (k / maxNames) * (STRIP_H - 4);
            const bw = Math.max(1, pw / Math.max(1, n) - 0.5);
            return (
              <rect
                key={dates[j]}
                className={`ni-bar${j === hover ? " on" : ""}`}
                x={x(j) - bw / 2}
                y={STRIP_H - h}
                width={bw}
                height={h}
              />
            );
          })}
          <text className="ni-axis" x={BIG.left - 6} y={STRIP_H - 2} textAnchor="end">
            names
          </text>
        </svg>
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
                <td className="ni-tk">{m.ticker}</td>
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
  onOpen,
  onSelect,
}: {
  c: NarrativeCardOut;
  data: NarrativeIndexOut;
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
        <MiniChart c={c} dates={data.dates} bench={data.benchmarks} />
      </button>
    </section>
  );
}

export default function NarrativeIndex({ asof, onSelectThesis }: DashboardProps) {
  const [win, setWin] = useState<IndexWindow>("1y");
  const [includeSilent, setIncludeSilent] = useState(false);
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [open, setOpen] = useState<string | null>(null);
  const q = useNarrativeIndex(asof, win, includeSilent);
  const data = q.data;

  const cards = useMemo(() => (data ? sortCards(data.cards, sort) : []), [data, sort]);

  if (q.isLoading) {
    return (
      <div className="dash-note">
        Computing every card's index — the first load after a run or a basket edit can take a minute.
      </div>
    );
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Narrative Index.</div>;

  const onSort = (key: SortKey) => setSort((s) => nextSort(s, key));

  return (
    <div className="ni">
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
        Each chart has its own scale; the faint flat line is 100. Faint dashed = SPY, dotted = IWM; the vertical
        tick marks the card's creation (the last session on or before that day), when it falls inside the
        window. Click a chart to enlarge it; click a name to open the card.
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
                onOpen={() => setOpen(c.thesis_id)}
                onSelect={onSelectThesis}
              />
            ),
          )}
        </div>
      )}
    </div>
  );
}
