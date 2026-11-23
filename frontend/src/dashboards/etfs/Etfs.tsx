import { useMemo, useState, type ReactNode } from "react";

import { FlaggedNote, flaggedRule } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./etfs.css";
import {
  COMPARE_SORT_OPTIONS,
  DEFAULT_COMPARE_SORT,
  DEFAULT_TREND_SORT,
  WINDOW_OPTIONS,
  DEFAULT_WINDOW,
  flowShare,
  addDays,
  fmtDay,
  fmtShortDay,
  fmtPct,
  fmtPts,
  fmtRange,
  fmtRatio,
  fmtSignedUsd,
  fmtUsd,
  linePath,
  logShare,
  nextCompareSort,
  nextTrendSort,
  plural,
  readingTitle,
  sortCompare,
  sortTrends,
  windowLabel,
  yAt,
  yDomain,
  type CompareSort,
  type CompareSortKey,
  type TrendSort,
  type TrendSortKey,
} from "./etfsView";
import {
  useEtfs,
  type EtfCardRefOut,
  type EtfCompareRowOut,
  type EtfFlowRowOut,
  type EtfReadingOut,
  type EtfSizeRowOut,
  type EtfTrendRowOut,
  type EtfWindow,
  type EtfsOut,
} from "./useEtfs";

// ETFs (docs/dashboards/etfs.md): the funds on the cards from five angles, each its own block with its count, its
// lines and a quiet note on how it was computed. Display only: every number (the returns, the flows, the sizes, the
// readings, who counts) is computed server-side; this page picks the window, orders the rows and draws them. The
// minis are inline SVG (never one chart-library instance per row). A panel with nothing to show says so in a quiet
// sentence and stays on the page. Quiet by construction (invariant #7 / WB#3): neutral tokens, a sign shown as +
// or −, and only panel 2's flow bars carry the sign colors.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";

type Select = DashboardProps["onSelectThesis"];

function cacheWords(d: EtfsOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet "show the work" line under the title, every number off the payload (rule 4). */
function workLine(d: EtfsOut): string {
  const fallback =
    d.roster_fallback_cards > 0
      ? `, ${d.roster_fallback_cards.toLocaleString("en-US")} reading today's roster for want of a snapshot`
      : "";
  const later =
    d.cards_not_yet_created > 0 ? ` (${d.cards_not_yet_created} created after the as-of left out)` : "";
  return [
    `${plural(d.cards_counted, "live card", "live cards")} counted${later}${fallback}`,
    `${plural(d.funds, "fund", "funds")} found (a fund = a row whose security is an ETF, in any link)`,
    "Context and Bench names are not a card's companies",
    `${plural(d.companies, "company", "companies")} on the cards with a fund`,
    `as of ${fmtDay(d.asof)}`,
    cacheWords(d),
  ].join(" · ");
}

/** A fund's ticker and name, with the cards it sits on: each card opens its Cockpit at the fund. */
function FundCell({
  ticker,
  name,
  cards,
  onSelect,
}: {
  ticker: string;
  name: string | null;
  cards: readonly EtfCardRefOut[];
  onSelect: Select;
}) {
  return (
    <div className="et-fund">
      <span className="et-tk" title={name ?? undefined}>
        {ticker}
      </span>
      {name && <span className="et-name">{name}</span>}
      <span className="et-cards">
        {cards.map((c) => (
          <button
            key={c.thesis_id}
            type="button"
            className="et-link"
            title={`Open ${c.thesis_name} on ${c.ticker}`}
            onClick={() => onSelect(c.thesis_id, c.ticker)}
          >
            {c.thesis_name}
          </button>
        ))}
      </span>
    </div>
  );
}

function Panel({
  id,
  title,
  count,
  note,
  wide,
  children,
}: {
  id: string;
  title: string;
  count?: string;
  note?: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <section className={`et-panel${wide ? " wide" : ""}`} aria-labelledby={`et-h-${id}`} data-testid={`et-panel-${id}`}>
      <h2 className="et-h" id={`et-h-${id}`}>
        <span className="et-title">{title}</span>
        {count && <span className="et-ct">{count}</span>}
      </h2>
      {children}
      {note && <p className="et-how">{note}</p>}
    </section>
  );
}

const Empty = ({ children }: { children: string }) => <p className="et-empty">{children}</p>;

// --- panel 1 --------------------------------------------------------------------------------------------------

const MINI_W = 148;
const MINI_H = 34;

function Mini({
  row,
  dates,
  sharedDomain,
  hoverIdx,
  onHover,
}: {
  row: EtfCompareRowOut;
  dates: readonly string[];
  sharedDomain: readonly [number, number] | null;
  hoverIdx: number | null;
  onHover: (idx: number | null) => void;
}) {
  const domain = sharedDomain ?? yDomain([row.fund_points, row.company_points]);
  const base = yAt(100, domain, MINI_H);
  const n = Math.max(row.fund_points?.length ?? 0, row.company_points?.length ?? 0);
  const active = hoverIdx !== null && n > 1 ? Math.max(0, Math.min(n - 1, hoverIdx)) : null;
  const hx = active !== null && n > 1 ? (active / (n - 1)) * MINI_W : null;
  const fPt = active !== null && row.fund_points && active < row.fund_points.length ? row.fund_points[active] : null;
  const cPt = active !== null && row.company_points && active < row.company_points.length ? row.company_points[active] : null;
  const fLast = row.fund_points && row.fund_points.length > 1 ? row.fund_points[row.fund_points.length - 1] : null;
  const cLast = row.company_points && row.company_points.length > 1 ? row.company_points[row.company_points.length - 1] : null;

  const handleMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (n <= 1) return;
    const rect = e.currentTarget.getBoundingClientRect();
    if (rect.width <= 0) return;
    const frac = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    onHover(Math.round(frac * (n - 1)));
  };

  return (
    <div className="et-mini-wrap">
      <svg
        className="et-mini"
        viewBox={`0 0 ${MINI_W} ${MINI_H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`${row.ticker} against the companies of ${row.thesis_name}`}
        onMouseMove={handleMove}
        onMouseLeave={() => onHover(null)}
      >
        <line className="et-base" x1={0} x2={MINI_W} y1={base} y2={base} />
        {row.company_points && row.company_points.length > 1 && (
          <path className="et-comp" d={linePath(row.company_points, domain, MINI_W, MINI_H)} />
        )}
        {row.fund_points && row.fund_points.length > 1 && (
          <path className="et-fline" d={linePath(row.fund_points, domain, MINI_W, MINI_H)} />
        )}
        {hx !== null && <line className="et-xhair" x1={hx} x2={hx} y1={0} y2={MINI_H} />}
        {active !== null ? (
          <>
            {cPt != null && hx !== null && <circle className="et-dot comp" cx={hx} cy={yAt(cPt, domain, MINI_H)} r={2.2} />}
            {fPt != null && hx !== null && <circle className="et-dot fund" cx={hx} cy={yAt(fPt, domain, MINI_H)} r={2.6} />}
          </>
        ) : (
          <>
            {cLast != null && <circle className="et-dot comp" cx={MINI_W} cy={yAt(cLast, domain, MINI_H)} r={1.8} />}
            {fLast != null && <circle className="et-dot fund" cx={MINI_W} cy={yAt(fLast, domain, MINI_H)} r={2.2} />}
          </>
        )}
      </svg>
      <span className="et-mini-read" data-testid={active !== null ? "et-mini-read" : undefined} aria-hidden={active === null || undefined}>
        {active !== null ? (
          <>
            {dates[active] ? fmtShortDay(dates[active]) : `#${active + 1}`}
            {fPt != null ? ` · F ${fmtPct(fPt - 100)}` : ""}
            {cPt != null ? ` · C ${fmtPct(cPt - 100)}` : ""}
          </>
        ) : (
          "\u00a0"
        )}
      </span>
    </div>
  );
}

function CompareRow({
  row,
  dates,
  sharedDomain,
  hoverIdx,
  onHover,
  onSelect,
}: {
  row: EtfCompareRowOut;
  dates: readonly string[];
  sharedDomain: readonly [number, number] | null;
  hoverIdx: number | null;
  onHover: (idx: number | null) => void;
  onSelect: Select;
}) {
  const missing = row.companies_ret == null ? "no priced company" : undefined;
  return (
    <li className="et-crow" data-testid="et-compare-row">
      <div className="et-who">
        <button
          type="button"
          className="et-card"
          title={`Open ${row.thesis_name}`}
          onClick={() => onSelect(row.thesis_id)}
        >
          {row.thesis_name}
        </button>
        <button
          type="button"
          className="et-link et-ftk"
          title={`Open ${row.thesis_name} on ${row.ticker}${row.fund_name ? ` (${row.fund_name})` : ""}`}
          onClick={() => onSelect(row.thesis_id, row.ticker)}
        >
          {row.ticker}
        </button>
      </div>
      <div className="et-nums">
        <span className="et-num-cell" title={row.note ?? "the fund's return over the window"}>
          fund {row.ret == null ? (row.note ?? "—") : fmtPct(row.ret)}
        </span>
        <span className="et-num-cell" title={missing ?? "the card's companies: equal weight, rebalanced daily"}>
          companies {fmtPct(row.companies_ret)}
        </span>
        <span className="et-num-cell et-diff-cell" title="the fund's return minus the companies', in percentage points">
          fund minus companies {fmtPts(row.diff)}
        </span>
        <span className="et-num-cell et-priced-cell" title="companies with at least one daily return in the window / companies on the card">
          {row.priced}/{row.companies} priced
          {row.sessions_flat > 0 && (
            <span className="et-flat" title="window sessions no company could contribute to: the companies' line carried flat">
              {" "}· {plural(row.sessions_flat, "session", "sessions")} flat
            </span>
          )}
        </span>
      </div>
      <Mini row={row} dates={dates} sharedDomain={sharedDomain} hoverIdx={hoverIdx} onHover={onHover} />
    </li>
  );
}

// --- panel 2 --------------------------------------------------------------------------------------------------

type FlowMetric = "1m_usd" | "1m_pct" | "1w_usd";
const FLOW_METRICS: readonly { key: FlowMetric; label: string }[] = [
  { key: "1m_usd", label: "1M $" },
  { key: "1m_pct", label: "1M % shares" },
  { key: "1w_usd", label: "1W $" },
];

function flowMetricVal(r: EtfFlowRowOut, metric: FlowMetric): number | null {
  if (metric === "1m_pct") return r.flow_1m_pct.value;
  if (metric === "1w_usd") return r.flow_1w_usd.value;
  return r.flow_1m_usd.value;
}

function flowTitle(r: EtfFlowRowOut): string {
  const parts = [
    r.latest_sample ? `latest sample ${fmtDay(r.latest_sample)}${r.source ? ` from ${r.source}` : ""}` : "no shares sample yet",
    r.flow_1w_usd.note,
    r.flow_1m_usd.note,
  ];
  return parts.filter(Boolean).join(" · ");
}

function FlowNumbers({ label, usd, pct, filling }: {
  label: string;
  usd: EtfReadingOut;
  pct: EtfReadingOut;
  filling: boolean;
}) {
  if (usd.value == null) {
    return (
      <span className="et-flow-n" title={readingTitle(usd)}>
        {filling ? `${label} filling` : `${label} ${usd.note ?? "—"}`}
      </span>
    );
  }
  return (
    <span className="et-flow-n">
      {label} {fmtSignedUsd(usd.value)} ({fmtPct(pct.value)} of shares)
    </span>
  );
}

function FlowRow({
  r,
  metric,
  max,
  onSelect,
}: {
  r: EtfFlowRowOut;
  metric: FlowMetric;
  max: readonly (number | null)[];
  onSelect: Select;
}) {
  const v = flowMetricVal(r, metric);
  const share = flowShare(v, max);
  return (
    <li className="et-frow" data-testid="et-flow-row" title={flowTitle(r)}>
      <FundCell ticker={r.ticker} name={r.name} cards={r.cards} onSelect={onSelect} />
      <div className="et-flow-bar" aria-hidden="true">
        {share != null && v != null && v !== 0 && (
          <span
            className={`et-bar ${v > 0 ? "pos" : "neg"}`}
            data-testid="et-flow-bar"
            style={v > 0 ? { left: "50%", width: `${share * 50}%` } : { right: "50%", width: `${share * 50}%` }}
          />
        )}
      </div>
      <div className="et-flow-text">
        {r.samples === 0 ? (
          <span className="et-flow-n">no shares sample yet</span>
        ) : (
          <>
            <FlowNumbers label="1W" usd={r.flow_1w_usd} pct={r.flow_1w_pct} filling={r.filling_1w} />
            <FlowNumbers label="1M" usd={r.flow_1m_usd} pct={r.flow_1m_pct} filling={r.filling_1m} />
          </>
        )}
      </div>
    </li>
  );
}

/** A fund with no flow number yet: samples exist, but both windows are still filling. */
const stillFilling = (r: EtfFlowRowOut): boolean =>
  r.samples > 0 && r.filling_1w && r.filling_1m && r.flow_1w_usd.value == null && r.flow_1m_usd.value == null;

/** The funds with no flow number yet, grouped into one quiet line per first-sample date; each ticker opens its
 *  card at the fund (listed, never dropped). */
function FillingLines({ rows, onSelect }: { rows: readonly EtfFlowRowOut[]; onSelect: Select }) {
  const groups = new Map<string, EtfFlowRowOut[]>();
  for (const r of rows) {
    const k = r.first_sample ?? "";
    groups.set(k, [...(groups.get(k) ?? []), r]);
  }
  return (
    <>
      {[...groups.entries()]
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([first, rs]) => (
          <p key={first} className="et-filling" data-testid="et-filling">
            Still filling, sampled since {fmtDay(first)}: 1W reads from about {fmtShortDay(addDays(first, 7))}, 1M from
            about {fmtShortDay(addDays(first, 30))} · {plural(rs.length, "fund", "funds")}:{" "}
            {rs.map((r, i) => (
              <span key={r.security_id}>
                {i > 0 && ", "}
                <button
                  type="button"
                  className="et-link"
                  title={`Open ${r.cards[0]?.thesis_name ?? "the card"} on ${r.ticker}`}
                  disabled={r.cards.length === 0}
                  onClick={() => r.cards[0] && onSelect(r.cards[0].thesis_id, r.cards[0].ticker)}
                >
                  {r.ticker}
                </button>
              </span>
            ))}
          </p>
        ))}
    </>
  );
}

// --- panel 3 --------------------------------------------------------------------------------------------------

type SizeSort = "size" | "adv" | "turnover";
const SIZE_SORTS: readonly { key: SizeSort; label: string }[] = [
  { key: "size", label: "Size" },
  { key: "adv", label: "ADV" },
  { key: "turnover", label: "Turnover" },
];

function turnoverPct(r: EtfSizeRowOut): number | null {
  if (r.size_usd == null || r.size_usd <= 0 || r.adv_usd_20d.value == null) return null;
  return (r.adv_usd_20d.value / r.size_usd) * 100;
}

function SizeRow({ r, sizes, advs, onSelect }: {
  r: EtfSizeRowOut;
  sizes: readonly (number | null)[];
  advs: readonly (number | null)[];
  onSelect: Select;
}) {
  const sz = logShare(r.size_usd, sizes);
  const av = logShare(r.adv_usd_20d.value, advs);
  const turn = turnoverPct(r);
  return (
    <li className="et-srow" data-testid="et-size-row">
      <FundCell ticker={r.ticker} name={r.name} cards={r.cards} onSelect={onSelect} />
      <div className="et-bars">
        <div className="et-track" title={r.size_note ?? undefined}>
          {sz != null && <span className="et-bar size" style={{ width: `${sz * 100}%` }} />}
          <span className="et-bar-t">
            {r.size_usd != null
              ? `approx. ${fmtUsd(r.size_usd)} (${fmtDay(r.sample_date)})`
              : (r.size_note ?? "no shares sample yet")}
          </span>
        </div>
        <div className="et-track" title={readingTitle(r.adv_usd_20d)}>
          {av != null && <span className="et-bar adv" style={{ width: `${av * 100}%` }} />}
          <span className="et-bar-t">
            {r.adv_usd_20d.value != null
              ? `${fmtUsd(r.adv_usd_20d.value)} a day (20d)${turn != null ? ` · ${turn.toFixed(1)}%/d` : ""}`
              : (r.adv_usd_20d.note ?? "—")}
          </span>
        </div>
      </div>
    </li>
  );
}

// --- panel 4 --------------------------------------------------------------------------------------------------

const TREND_COLUMNS: readonly {
  key: Exclude<TrendSortKey, "ticker">;
  label: string;
  title: string;
  fmt: (v: number | null) => string;
}[] = [
  { key: "ret_30d", label: "30d", title: "return over the last 30 trading days (the Cockpit's 30d column)", fmt: (v) => fmtPct(v) },
  { key: "vs_50d", label: "vs 50d", title: "distance from the 50-day average", fmt: (v) => fmtPct(v) },
  { key: "vs_200d", label: "vs 200d", title: "distance from the 200-day average", fmt: (v) => fmtPct(v) },
  { key: "rvol20", label: "RVOL 20", title: "today's volume over the 20-day average", fmt: fmtRatio },
  { key: "range_pos", label: "52w range", title: "position in the 52-week range: 0 at the low, 100 at the high", fmt: fmtRange },
];

function RangeTrack({ value }: { value: number | null }) {
  if (value == null) return null;
  const pct = Math.max(0, Math.min(100, value));
  return (
    <span className="et-rtrack" aria-hidden="true">
      <span className="et-rmid" />
      <span className="et-rdot" style={{ left: `${pct}%` }} />
    </span>
  );
}

function TrendTable({ rows, sort, onSort, onSelect }: {
  rows: readonly EtfTrendRowOut[];
  sort: TrendSort;
  onSort: (k: TrendSortKey) => void;
  onSelect: Select;
}) {
  const arrow = (k: TrendSortKey) => (sort.key === k ? (sort.dir === "asc" ? " ▲" : " ▼") : "");
  return (
    <div className="et-trend-wrap">
    <table className="et-trend">
      <thead>
        <tr>
          <th scope="col">
            <button type="button" className="et-th" aria-pressed={sort.key === "ticker"} onClick={() => onSort("ticker")}>
              Fund{arrow("ticker")}
            </button>
          </th>
          {TREND_COLUMNS.map((c) => (
            <th key={c.key} scope="col" className="et-num" title={c.title}>
              <button type="button" className="et-th" aria-pressed={sort.key === c.key} onClick={() => onSort(c.key)}>
                {c.label}
                {arrow(c.key)}
              </button>
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.security_id} data-testid="et-trend-row">
            <td>
              <FundCell ticker={r.ticker} name={null} cards={r.cards} onSelect={onSelect} />
            </td>
            {TREND_COLUMNS.map((c) => {
              const reading = r[c.key];
              return (
                <td key={c.key} className="et-num" title={readingTitle(reading)}>
                  {c.key === "range_pos" && reading.value != null ? (
                    <span className="et-rcell">
                      <RangeTrack value={reading.value} />
                      <span>{c.fmt(reading.value)}</span>
                    </span>
                  ) : (
                    c.fmt(reading.value)
                  )}
                </td>
              );
            })}
          </tr>
        ))}
      </tbody>
    </table>
    </div>
  );
}

// --- the page -------------------------------------------------------------------------------------------------

export default function Etfs({ asof, onSelectThesis }: DashboardProps) {
  const [win, setWin] = useState<EtfWindow>(DEFAULT_WINDOW);
  const [sort, setSort] = useState<CompareSort>(DEFAULT_COMPARE_SORT);
  const [trendSort, setTrendSort] = useState<TrendSort>(DEFAULT_TREND_SORT);
  const [sharedScale, setSharedScale] = useState(false);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);
  const [flowMetric, setFlowMetric] = useState<FlowMetric>("1m_usd");
  const [sizeSort, setSizeSort] = useState<SizeSort>("size");
  const [query, setQuery] = useState("");
  const q = useEtfs(asof, win);
  const data = q.data;

  const qNorm = query.trim().toLowerCase();
  const compare = useMemo(() => {
    if (!data) return [];
    const sorted = sortCompare(data.compare, sort);
    if (!qNorm) return sorted;
    return sorted.filter(
      (r) =>
        r.ticker.toLowerCase().includes(qNorm) ||
        r.thesis_name.toLowerCase().includes(qNorm) ||
        (r.fund_name ?? "").toLowerCase().includes(qNorm),
    );
  }, [data, sort, qNorm]);
  const sharedDomain = useMemo(() => {
    if (!sharedScale || compare.length === 0) return null;
    const allLines = compare.flatMap((r) => [r.fund_points, r.company_points]);
    return yDomain(allLines);
  }, [sharedScale, compare]);
  const trends = useMemo(() => (data ? sortTrends(data.trends, trendSort) : []), [data, trendSort]);

  if (q.isLoading) {
    return (
      <div className="dash-note">
        Reading the funds and their cards' companies — the first load after a run or a basket edit can take a
        moment.
      </div>
    );
  }
  if (q.error || !data) return <div className="dash-note">Could not load the ETFs page.</div>;

  const flowValues = data.flows.map((r) => flowMetricVal(r, flowMetric));
  const flowAbsMax = Math.max(0, ...flowValues.map((v) => Math.abs(v ?? 0)));
  const sizes = data.sizes.map((r) => r.size_usd);
  const advs = data.sizes.map((r) => r.adv_usd_20d.value);
  const sortedSizes = [...data.sizes].sort((a, b) => {
    const val = (r: EtfSizeRowOut) =>
      sizeSort === "adv" ? r.adv_usd_20d.value : sizeSort === "turnover" ? turnoverPct(r) : r.size_usd;
    const va = val(a);
    const vb = val(b);
    if (va == null && vb == null) return a.ticker.localeCompare(b.ticker);
    if (va == null) return 1;
    if (vb == null) return -1;
    return vb - va || a.ticker.localeCompare(b.ticker);
  });
  const baseFlowRows = data.flows.filter((r) => !stillFilling(r));
  const flowRows =
    flowMetric === "1m_usd"
      ? baseFlowRows
      : [...baseFlowRows].sort((a, b) => {
          const va = flowMetricVal(a, flowMetric);
          const vb = flowMetricVal(b, flowMetric);
          if (va == null && vb == null) return a.ticker.localeCompare(b.ticker);
          if (va == null) return 1;
          if (vb == null) return -1;
          return Math.abs(vb) - Math.abs(va) || a.ticker.localeCompare(b.ticker);
        });
  const fillingRows = data.flows.filter(stillFilling);
  const anySample = data.flows.some((r) => r.samples > 0);
  const flaggedAll = [...data.flagged_funds, ...data.flagged_companies];
  const cov = data.coverage;
  const covPct = cov.cards_counted > 0 ? Math.round((cov.cards_with_fund / cov.cards_counted) * 100) : 0;
  const flaggedRule1 = flaggedRule("they are left out of both lines and every number on this panel.");

  return (
    <div className="et" data-testid="etfs">
      <div className="dash-note et-work">{workLine(data)}</div>
      {data.roster_fallback_cards > 0 && (
        <div className="dash-note et-work">
          Today's roster stands in for {plural(data.roster_fallback_cards, "card", "cards")}: rosters are
          point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
        </div>
      )}

      <Panel
        id="compare"
        wide
        title="Fund vs its companies"
        count={plural(data.compare.length, "pair", "pairs")}
        note="Each pair is a fund and the card it sits on. The fund's line is its closes from 100 at the window start; the companies' line is equal weight, rebalanced daily, from 100 (the card's voting names, every fund left out). The two moves and their difference, nothing more."
      >
        <div className="et-controls">
          <div className="et-seg" role="group" aria-label="Window">
            <span className="et-seg-label">Window</span>
            {WINDOW_OPTIONS.map((o) => (
              <button
                key={o.value}
                type="button"
                className={`et-seg-btn${win === o.value ? " on" : ""}`}
                aria-pressed={win === o.value}
                onClick={() => setWin(o.value)}
              >
                {o.label}
              </button>
            ))}
          </div>
          <div className="et-seg" role="group" aria-label="Sort">
            <span className="et-seg-label">Sort</span>
            {COMPARE_SORT_OPTIONS.map((o) => {
              const on = sort.key === o.value;
              return (
                <button
                  key={o.value}
                  type="button"
                  className={`et-seg-btn${on ? " on" : ""}`}
                  aria-pressed={on}
                  title={on ? "Pick again to reverse" : undefined}
                  onClick={() => setSort((s) => nextCompareSort(s, o.value as CompareSortKey))}
                >
                  {o.label}
                  {on && <span className="et-arrow" aria-hidden="true">{sort.dir === "asc" ? "▲" : "▼"}</span>}
                </button>
              );
            })}
          </div>
          <button
            type="button"
            className={`et-seg-btn${sharedScale ? " on" : ""}`}
            aria-pressed={sharedScale}
            title="Put every sparkline on the same vertical scale"
            onClick={() => setSharedScale((s) => !s)}
          >
            Shared scale
          </button>
          <input
            type="search"
            className="et-search"
            aria-label="Filter fund or card"
            placeholder="Filter fund or card…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <span className="et-key" aria-hidden="true">
            <span className="et-key-item">
              <span className="et-key-line fund" /> Fund
            </span>
            <span className="et-key-item">
              <span className="et-key-line comp" /> Companies
            </span>
          </span>
          <span className="et-iwm" title="IWM's return over the same sessions">
            {data.iwm.available ? `IWM ${fmtPct(data.iwm.ret)}` : `IWM ${data.iwm.reason ?? "absent"}`} over{" "}
            {windowLabel(data.window)}: {plural(data.sessions, "session", "sessions")}
            {data.dates.length > 0 ? `, ${fmtDay(data.dates[0])} → ${fmtDay(data.dates[data.dates.length - 1])}` : ""}
          </span>
          {q.isFetching && <span className="dash-note">updating…</span>}
        </div>
        {compare.length === 0 ? (
          <Empty>
            {qNorm
              ? "No fund or card matches that filter."
              : data.funds === 0
                ? "No card has a fund at this as-of."
                : "No fund has a line at this as-of: every fund is left out for a flagged price history."}
          </Empty>
        ) : (
          <ul className="et-list">
            {compare.map((r) => (
              <CompareRow
                key={`${r.thesis_id}:${r.security_id}`}
                row={r}
                dates={data.dates}
                sharedDomain={sharedDomain}
                hoverIdx={hoverIdx}
                onHover={setHoverIdx}
                onSelect={onSelectThesis}
              />
            ))}
          </ul>
        )}
        <FlaggedNote flagged={flaggedAll} title={flaggedRule1} />
      </Panel>

      <div className="et-grid">
        <Panel
          id="flow"
          title="Money in and out"
          count={plural(data.flows.length, "fund", "funds")}
          note="Net creations minus redemptions: the change in the fund's own shares between samples, priced at the close on or before each sample, over the trailing 7 and 30 days. Shares flat means zero flow, whatever the price did. The bar scales to the active metric: left for outflow, right for inflow."
        >
          {data.flows.length === 0 ? (
            <Empty>No fund to show.</Empty>
          ) : (
            <>
              {!anySample && <Empty>No fund has a shares sample yet.</Empty>}
              {flowRows.length > 0 && (
                <>
                  <div className="et-panel-bar">
                    <div className="et-seg" role="group" aria-label="Flow bar metric">
                      <span className="et-seg-label">Bar</span>
                      {FLOW_METRICS.map((m) => (
                        <button
                          key={m.key}
                          type="button"
                          className={`et-seg-btn${flowMetric === m.key ? " on" : ""}`}
                          aria-pressed={flowMetric === m.key}
                          onClick={() => setFlowMetric(m.key)}
                        >
                          {m.label}
                        </button>
                      ))}
                    </div>
                    {flowAbsMax > 0 && (
                      <span className="et-axis-scale" aria-hidden="true">
                        <span>{flowMetric === "1m_pct" ? `−${flowAbsMax.toFixed(1)}%` : `−${fmtUsd(flowAbsMax)}`}</span>
                        <span>0</span>
                        <span>{flowMetric === "1m_pct" ? `+${flowAbsMax.toFixed(1)}%` : `+${fmtUsd(flowAbsMax)}`}</span>
                      </span>
                    )}
                  </div>
                  <ul className="et-list">
                    {flowRows.map((r) => (
                      <FlowRow key={r.security_id} r={r} metric={flowMetric} max={flowValues} onSelect={onSelectThesis} />
                    ))}
                  </ul>
                </>
              )}
              <FillingLines rows={fillingRows} onSelect={onSelectThesis} />
            </>
          )}
          <FlaggedNote flagged={data.flagged_funds} />
        </Panel>

        <Panel
          id="size"
          title="Size and trading"
          count={plural(data.sizes.length, "fund", "funds")}
          note="Approximate size is the latest shares sample times the close on or before its date, not the fund's filed net assets. The dollar volume is the 20-day average of close times volume. Both bars are on log scales."
        >
          {data.sizes.length === 0 ? (
            <Empty>No fund to show.</Empty>
          ) : (
            <>
              <div className="et-panel-bar">
                <div className="et-seg" role="group" aria-label="Sort size">
                  <span className="et-seg-label">Order</span>
                  {SIZE_SORTS.map((s) => (
                    <button
                      key={s.key}
                      type="button"
                      className={`et-seg-btn${sizeSort === s.key ? " on" : ""}`}
                      aria-pressed={sizeSort === s.key}
                      onClick={() => setSizeSort(s.key)}
                    >
                      {s.label}
                    </button>
                  ))}
                </div>
              </div>
              <ul className="et-list">
                {sortedSizes.map((r) => (
                  <SizeRow key={r.security_id} r={r} sizes={sizes} advs={advs} onSelect={onSelectThesis} />
                ))}
              </ul>
            </>
          )}
          <FlaggedNote flagged={data.flagged_funds} />
        </Panel>

        <Panel
          id="trend"
          title="Price trend"
          count={plural(data.trends.length, "fund", "funds")}
          note="The readings the Cockpit's basket table shows for any name, with the same notes on hover. The 52-week range column runs 0 at the low to 100 at the high."
        >
          {data.trends.length === 0 ? (
            <Empty>No fund to show.</Empty>
          ) : (
            <TrendTable
              rows={trends}
              sort={trendSort}
              onSort={(k) => setTrendSort((s) => nextTrendSort(s, k))}
              onSelect={onSelectThesis}
            />
          )}
          <FlaggedNote flagged={data.flagged_funds} />
        </Panel>

        <Panel
          id="coverage"
          title="Coverage"
          count={`${cov.cards_with_fund} of ${cov.cards_counted} cards`}
          note="A flagged price history leaves a fund out of the price panels, not out of this one."
        >
          <p className="et-line">
            {cov.cards_with_fund} of {plural(cov.cards_counted, "card", "cards")} have a fund
            {cov.funds > 0 ? ` (${plural(cov.funds, "fund", "funds")})` : ""}.
          </p>
          {cov.cards_counted > 0 && (
            <div className="et-cov-bar" aria-hidden="true" title={`${covPct}% of cards have a fund`}>
              <span className="et-cov-fill" style={{ width: `${covPct}%` }} />
            </div>
          )}
          <h3 className="et-sub">Cards with no fund</h3>
          {cov.without.length === 0 ? (
            <Empty>Every card has a fund.</Empty>
          ) : (
            <ul className="et-chips">
              {cov.without.map((c) => (
                <li key={c.thesis_id}>
                  <button
                    type="button"
                    className="et-chip"
                    title={`Open ${c.thesis_name}`}
                    onClick={() => onSelectThesis(c.thesis_id)}
                  >
                    {c.thesis_name}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <h3 className="et-sub">Funds on more than one card</h3>
          {cov.multi.length === 0 ? (
            <Empty>No fund sits on more than one card.</Empty>
          ) : (
            <ul className="et-plain">
              {cov.multi.map((f) => (
                <li key={f.security_id}>
                  <span className="et-tk">{f.ticker}</span>{" "}
                  {f.cards.map((c) => (
                    <button
                      key={c.thesis_id}
                      type="button"
                      className="et-link"
                      title={`Open ${c.thesis_name} on ${c.ticker}`}
                      onClick={() => onSelectThesis(c.thesis_id, c.ticker)}
                    >
                      {c.thesis_name}
                    </button>
                  ))}
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}
