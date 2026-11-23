import { useMemo, useState, type ReactNode } from "react";

import { fmtDate } from "../../util/format";
import { FlaggedNote, leftOutWords } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./cross-radar.css";
import {
  CLUSTER_SORT,
  GROUP_BY_OPTIONS,
  MOVER_SORT,
  NEAR_SORT,
  POSTURES,
  TIMELINE_SORT,
  clusterFirstDir,
  fmtDay,
  fmtGap,
  fmtPts,
  groupLabel,
  moverFirstDir,
  nearFirstDir,
  nextSort,
  pct,
  plural,
  postureTotal,
  sessionsAgo,
  sortClusters,
  sortMovers,
  sortNear,
  sortTimeline,
  timelineFirstDir,
  type ClusterCol,
  type MoverCol,
  type NearCol,
  type Sort,
  type SortDir,
  type TimelineCol,
} from "./crossView";
import {
  useCrossRadar,
  type CrossCardOut,
  type CrossMarkOut,
  type CrossPostureOut,
  type CrossRadarOut,
  type CrossTimelineNameOut,
  type CrossTimelineRowOut,
  type GroupBy,
} from "./useCrossRadar";

// Cross Radar (docs/dashboards/cross-radar.md): golden and death crosses (the 50-day SMA crossing the 200-day)
// by group across every live card, at the as-of. Display only. Every number (the crosses, the cluster and
// first-mover rules, the near-cross band, the posture counts) is computed server-side; this page picks the
// view, orders each list and draws the timeline. Loudness is reserved for clusters (#7 / WB#3): first movers
// carry a quiet accent, everything else stays gray.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";

const ARROW = { golden: "↑", death: "↓", up: "↑", down: "↓" } as const;

function cacheWords(d: CrossRadarOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet line that shows the work, every number off the payload. */
function workLine(d: CrossRadarOut): string {
  const cards =
    d.cards_not_yet_created > 0
      ? `${plural(d.cards_counted, "live card", "live cards")} counted (${d.cards_not_yet_created} created after the as-of left out)`
      : `${plural(d.cards_counted, "live card", "live cards")} counted`;
  const notPriced = d.companies - d.priced_companies - d.flagged.length;
  return [
    cards,
    plural(d.companies, "company", "companies"),
    `${d.priced_companies.toLocaleString("en-US")} priced`,
    `${notPriced.toLocaleString("en-US")} not priced (${d.short_tape_companies} too few bars, ${d.stale_tape_companies} stale tape)`,
    plural(d.timeline.length, "group", "groups"),
    d.include_silent
      ? "Context and Bench included"
      : `Context and Bench left out (${plural(d.silent_rows_left_out, "basket row", "basket rows")})`,
    `${plural(d.unresolved_rows, "basket row", "basket rows")} without a resolved security not counted`,
    d.price_200d ? "price × 200d ticks on" : "price × 200d ticks off",
    `cluster: ≥ ${d.cluster_min_names} names and ≥ ${d.cluster_min_share_pct}% of the priced names within ${d.recent_sessions} sessions`,
    `near cross: within ±${d.near_band_pct}% and narrowing over ${d.near_lookback} sessions`,
    cacheWords(d),
    `as of ${fmtDay(d.asof)}`,
  ].join(" · ");
}

function SortTh<C extends string>({
  col,
  label,
  title,
  sort,
  onSort,
  num = true,
}: {
  col: C;
  label: string;
  title: string;
  sort: Sort<C>;
  onSort: (c: C) => void;
  num?: boolean;
}) {
  const dir: SortDir | null = sort.col === col ? sort.dir : null;
  return (
    <th
      className={num ? "xr-num" : undefined}
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
    >
      <button type="button" className={`xr-sort${dir ? " active" : ""}`} title={title} onClick={() => onSort(col)}>
        {label}
        {dir && (
          <span className="xr-arrow" aria-hidden="true">
            {dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    </th>
  );
}

/** A name: its ticker opens its (first) card's Cockpit on it; outside the Link / Card views, or on several
 *  cards, each card is its own chip. */
function NameLink({
  ticker,
  cards,
  groupBy,
  onSelect,
}: {
  ticker: string;
  cards: readonly CrossCardOut[];
  groupBy: GroupBy;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const first = cards[0];
  const chips = cards.length > 1 || groupBy === "business_type" || groupBy === "sector";
  return (
    <span className="xr-nm">
      <button
        type="button"
        className="xr-tk"
        title={first ? `Open ${first.thesis_name} on ${first.ticker}` : undefined}
        onClick={() => first && onSelect(first.thesis_id, first.ticker)}
      >
        {ticker}
      </button>
      {chips &&
        cards.map((c) => {
          const links = c.links.map((l) => l ?? "Unsegmented").join(", ");
          return (
            <button
              key={c.thesis_id}
              type="button"
              className="xr-chip"
              title={`Open ${c.thesis_name} on ${c.ticker}`}
              onClick={() => onSelect(c.thesis_id, c.ticker)}
            >
              {c.thesis_name}
              {groupBy !== "link" && links && <span className="xr-chip-links"> · {links}</span>}
            </button>
          );
        })}
    </span>
  );
}

function Section({
  id,
  title,
  count,
  rule,
  children,
}: {
  id?: string;
  title: string;
  count?: number;
  rule: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="xr-sec" aria-label={title}>
      <div className="xr-sec-head">
        <h2 className="xr-h">{title}</h2>
        {count !== undefined && (
          <span className={`xr-h-count${count > 0 ? " has" : ""}`} aria-hidden="true">
            {count.toLocaleString("en-US")}
          </span>
        )}
        <span className="xr-h-rule" aria-hidden="true" />
      </div>
      <div className="dash-note xr-rule">{rule}</div>
      {children}
    </section>
  );
}

// --- 1. clusters (loud: exceptions only) --------------------------------------------------------------

function Clusters({
  data,
  query,
  onSelect,
}: {
  data: CrossRadarOut;
  query: string;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const [sort, setSort] = useState<Sort<ClusterCol>>(CLUSTER_SORT);
  const view = data.group_by;
  const rows = useMemo(() => {
    const sorted = sortClusters(data.clusters, sort, view);
    if (!query) return sorted;
    return sorted.filter(
      (c) =>
        groupLabel(c.group, view).toLocaleLowerCase().includes(query) ||
        c.names.some((n) => n.ticker.toLocaleLowerCase().includes(query)),
    );
  }, [data.clusters, sort, view, query]);
  const onSort = (c: ClusterCol) => setSort((s) => nextSort(s, c, clusterFirstDir));
  return (
    <Section
      id="xr-clusters"
      title="Clusters"
      count={rows.length}
      rule={`A group clusters when at least ${data.cluster_min_names} names and at least ${data.cluster_min_share_pct}% of its priced names crossed the same way within the last ${data.recent_sessions} sessions. No weighting.`}
    >
      {rows.length === 0 ? (
        <div className="dash-note xr-empty">
          No group has {data.cluster_min_names}+ names and {data.cluster_min_share_pct}%+ of its priced names
          crossing the same way within {data.recent_sessions} sessions.
        </div>
      ) : (
        <div className="xr-scroll">
          <table className="xr-table" aria-label="Clusters">
            <thead>
              <tr>
                <SortTh col="label" label="Group" title="Sort by name" sort={sort} onSort={onSort} num={false} />
                <SortTh col="count" label="Crossed" title="Sort by names crossed" sort={sort} onSort={onSort} />
                <SortTh col="share" label="Share" title="Sort by the share of priced names crossed" sort={sort} onSort={onSort} />
                <SortTh col="within" label="Within" title="Sort by how recent the oldest cross is" sort={sort} onSort={onSort} />
                <SortTh col="rs_leaders" label="RS leaders" title="Sort by RS leaders among the names crossed" sort={sort} onSort={onSort} />
                <th>Names</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => (
                <tr key={`${c.group.key}:${c.direction}`} className={`xr-cluster ${c.direction}`}>
                  <td className="xr-label">{groupLabel(c.group, view)}</td>
                  <td className="xr-num">
                    <span className={`xr-dir ${c.direction}`}>
                      {c.count} of {c.priced} {c.direction} {ARROW[c.direction]}
                    </span>
                  </td>
                  <td className="xr-num xr-v">{pct(c.share)}</td>
                  <td className="xr-num xr-v">within {plural(c.within, "session", "sessions")}</td>
                  <td className="xr-num xr-v">
                    {c.rs_leaders} {c.rs_leaders === 1 ? "is an RS leader" : "are RS leaders"}
                  </td>
                  <td>
                    <span className="xr-names">
                      {c.names.map((n) => (
                        <span key={n.security_id} className="xr-name" title={sessionsAgo(n.sessions_ago)}>
                          <NameLink ticker={n.ticker} cards={n.cards} groupBy={view} onSelect={onSelect} />
                          <span className="xr-sub">{fmtDate(n.d)}</span>
                        </span>
                      ))}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// --- 2. first movers (quiet accent) ---------------------------------------------------------------------

function FirstMovers({
  data,
  query,
  onSelect,
}: {
  data: CrossRadarOut;
  query: string;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const [sort, setSort] = useState<Sort<MoverCol>>(MOVER_SORT);
  const view = data.group_by;
  const rows = useMemo(() => {
    const sorted = sortMovers(data.first_movers, sort, view);
    if (!query) return sorted;
    return sorted.filter(
      (f) =>
        groupLabel(f.group, view).toLocaleLowerCase().includes(query) ||
        f.leader.ticker.toLocaleLowerCase().includes(query),
    );
  }, [data.first_movers, sort, view, query]);
  const onSort = (c: MoverCol) => setSort((s) => nextSort(s, c, moverFirstDir));
  return (
    <Section
      id="xr-movers"
      title="First movers"
      count={rows.length}
      rule={`A group of ${data.first_mover_min_priced}+ priced names where exactly one crossed within the last ${data.recent_sessions} sessions ("first to roll over" for a death cross), with the rest counted.`}
    >
      {rows.length === 0 ? (
        <div className="dash-note xr-empty">
          No group has exactly one name crossing within {data.recent_sessions} sessions.
        </div>
      ) : (
        <div className="xr-scroll">
          <table className="xr-table" aria-label="First movers">
            <thead>
              <tr>
                <SortTh col="label" label="Group" title="Sort by name" sort={sort} onSort={onSort} num={false} />
                <th>Leader</th>
                <SortTh col="date" label="Crossed" title="Sort by the cross date" sort={sort} onSort={onSort} />
                <SortTh col="closing_in" label="Closing in" title="Sort by the names closing in" sort={sort} onSort={onSort} />
                <SortTh col="already" label="Already" title="Sort by the names already on the far side" sort={sort} onSort={onSort} />
                <SortTh col="not_yet" label="Not yet" title="Sort by the names not yet" sort={sort} onSort={onSort} />
              </tr>
            </thead>
            <tbody>
              {rows.map((f) => (
                <tr key={`${f.group.key}:${f.direction}`} className={`xr-mover ${f.direction}`}>
                  <td className="xr-label">{groupLabel(f.group, view)}</td>
                  <td>
                    <NameLink ticker={f.leader.ticker} cards={f.leader.cards} groupBy={view} onSelect={onSelect} />
                    <span className={`xr-dir quiet ${f.direction}`}>
                      {f.direction} {ARROW[f.direction]}
                    </span>
                    {f.direction === "death" && <span className="xr-tag">first to roll over</span>}
                    {f.leader.rs_leading && <span className="xr-tag">RS leader</span>}
                  </td>
                  <td className="xr-num xr-v" title={fmtDay(f.leader.d)}>
                    {sessionsAgo(f.leader.sessions_ago)}
                    <span className="xr-sub">{fmtDate(f.leader.d)}</span>
                  </td>
                  <td className="xr-num xr-v">
                    {f.closing_in} of {f.rest} closing in
                    {f.rest > 0 && (
                      <span className="xr-fm-bar" aria-hidden="true">
                        {f.already > 0 && (
                          <span className={`xr-fm-seg already ${f.direction}`} style={{ flexGrow: f.already }} />
                        )}
                        {f.closing_in > 0 && (
                          <span className={`xr-fm-seg closing ${f.direction}`} style={{ flexGrow: f.closing_in }} />
                        )}
                        {f.not_yet > 0 && <span className="xr-fm-seg notyet" style={{ flexGrow: f.not_yet }} />}
                      </span>
                    )}
                  </td>
                  <td className="xr-num xr-v">
                    {f.already} already {f.direction === "golden" ? "above" : "below"}
                  </td>
                  <td className="xr-num xr-v">{f.not_yet} not yet</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

// --- 3. the near-cross watch (quiet list) -------------------------------------------------------------

function NearCross({
  data,
  query,
  onSelect,
}: {
  data: CrossRadarOut;
  query: string;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const [sort, setSort] = useState<Sort<NearCol>>(NEAR_SORT);
  const view = data.group_by;
  const groups = useMemo(() => {
    const sorted = sortNear(data.near_cross, sort, view);
    if (!query) return sorted;
    return sorted
      .map((g) => {
        if (groupLabel(g.group, view).toLocaleLowerCase().includes(query)) return g;
        const names = g.names.filter((n) => n.ticker.toLocaleLowerCase().includes(query));
        return names.length > 0 ? { ...g, names } : null;
      })
      .filter((g): g is CrossRadarOut["near_cross"][number] => g !== null);
  }, [data.near_cross, sort, view, query]);
  const totalNames = useMemo(() => groups.reduce((acc, g) => acc + g.names.length, 0), [groups]);
  const onSort = (c: NearCol) => setSort((s) => nextSort(s, c, nearFirstDir));
  return (
    <Section
      id="xr-near"
      title="Near-cross watch"
      count={totalNames}
      rule={`50d within ±${data.near_band_pct}% of the 200d, the gap narrower than ${data.near_lookback} sessions ago, and no cross within ${data.recent_sessions} sessions. The tape as it stands, not a forecast.`}
    >
      {groups.length === 0 ? (
        <div className="dash-note xr-empty">No name is within ±{data.near_band_pct}% of a cross and narrowing.</div>
      ) : (
        <div className="xr-scroll">
          <table className="xr-table xr-near" aria-label="Near-cross watch">
            <thead>
              <tr>
                <SortTh col="label" label="Group / name" title="Sort by group name" sort={sort} onSort={onSort} num={false} />
                <th>Approaching</th>
                <SortTh col="gap" label="50d vs 200d" title="Sort by the gap (smallest first)" sort={sort} onSort={onSort} />
                <SortTh
                  col="change"
                  label={`${data.near_lookback}-session change`}
                  title="Sort by how far the gap moved"
                  sort={sort}
                  onSort={onSort}
                />
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <NearGroupRows key={g.group.key} g={g} band={data.near_band_pct} view={view} onSelect={onSelect} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

/** Compact ±band convergence track showing the 50d/200d gap moving from `then` toward `0%` now (zero text nodes). */
function NearTrack({
  gap,
  thenGap,
  band,
  toward,
}: {
  gap: number;
  thenGap: number;
  band: number;
  toward: "golden" | "death";
}) {
  const lim = Math.max(band, Math.abs(gap), Math.abs(thenGap), 0.5);
  const toPct = (v: number) => Math.max(4, Math.min(96, 50 + (v / lim) * 46));
  const xNow = toPct(gap);
  const xThen = toPct(thenGap);
  const left = Math.min(xNow, xThen);
  const width = Math.max(1, Math.abs(xNow - xThen));
  return (
    <span className="xr-near-track" aria-hidden="true">
      <span className="xr-near-zero" />
      <span className={`xr-near-trail ${toward}`} style={{ left: `${left}%`, width: `${width}%` }} />
      <span className="xr-near-then" style={{ left: `${xThen}%` }} />
      <span className={`xr-near-now ${toward}`} style={{ left: `${xNow}%` }} />
    </span>
  );
}

function NearGroupRows({
  g,
  band,
  view,
  onSelect,
}: {
  g: CrossRadarOut["near_cross"][number];
  band: number;
  view: GroupBy;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  return (
    <>
      <tr className="xr-near-group">
        <td className="xr-label" colSpan={4}>
          {groupLabel(g.group, view)}
        </td>
      </tr>
      {g.names.map((n) => (
        <tr key={n.security_id} className="xr-near-name">
          <td className="xr-name-id">
            <NameLink ticker={n.ticker} cards={n.cards} groupBy={view} onSelect={onSelect} />
          </td>
          <td className="xr-v">
            <span className={`xr-toward ${n.toward}`}>approaching {n.toward}</span>
          </td>
          <td className="xr-num xr-v">
            <span className="xr-gap-wrap">
              <NearTrack gap={n.gap_pct} thenGap={n.gap_pct_then} band={band} toward={n.toward} />
              <span>{fmtGap(n.gap_pct)}</span>
            </span>
          </td>
          <td className="xr-num xr-v" title={`${fmtGap(n.gap_pct_then)} → ${fmtGap(n.gap_pct)}`}>
            {fmtPts(n.change)}
          </td>
        </tr>
      ))}
    </>
  );
}

// --- 4 + 5. the cross timeline and the posture bar ------------------------------------------------------

type TaggedMark = CrossMarkOut & { ticker?: string };
type GroupedMark = {
  pos: number;
  d: string;
  direction: CrossMarkOut["direction"];
  sessions_ago: number;
  tickers: string[];
  count: number;
};

function groupMarks(marks: readonly TaggedMark[]): GroupedMark[] {
  const map = new Map<string, GroupedMark>();
  for (const m of marks) {
    const key = `${m.pos}:${m.direction}`;
    const existing = map.get(key);
    if (existing) {
      existing.count += 1;
      if (m.ticker && !existing.tickers.includes(m.ticker)) existing.tickers.push(m.ticker);
    } else {
      map.set(key, {
        pos: m.pos,
        d: m.d,
        direction: m.direction,
        sessions_ago: m.sessions_ago,
        tickers: m.ticker ? [m.ticker] : [],
        count: 1,
      });
    }
  }
  return [...map.values()];
}

function Strip({
  marks,
  layer,
  sessions,
  recentSessions,
  rowLabel,
  label,
  onDetail,
}: {
  marks: readonly TaggedMark[];
  layer: readonly TaggedMark[];
  sessions: number;
  recentSessions: number;
  rowLabel: string;
  label: string;
  onDetail: (text: string | null) => void;
}) {
  const n = Math.max(sessions, 1);
  const groupedMarks = useMemo(() => groupMarks(marks), [marks]);
  const groupedLayer = useMemo(() => groupMarks(layer), [layer]);
  const recentW = Math.min(n, Math.max(0, recentSessions));
  const recentX = n - recentW;

  const describeMark = (m: GroupedMark) => {
    const who = m.tickers.length > 0 ? ` — ${m.tickers.join(", ")}` : "";
    const qty = m.count > 1 ? `${m.count} ${m.direction} crosses` : `${m.direction} cross`;
    return `${qty} on ${fmtDate(m.d)} (${sessionsAgo(m.sessions_ago)})${who}`;
  };
  const describeLayer = (m: GroupedMark) => {
    const who = m.tickers.length > 0 ? ` — ${m.tickers.join(", ")}` : "";
    return `price crossed ${m.direction === "up" ? "above" : "below"} 200d on ${fmtDate(m.d)}${who}`;
  };

  return (
    <svg
      className="xr-strip"
      viewBox={`0 0 ${n} 16`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
    >
      {recentW > 0 && recentW < n && (
        <rect className="xr-recent-band" x={recentX} y={0} width={recentW} height={16} />
      )}
      <line className="xr-base" x1={0} x2={n} y1={8} y2={8} vectorEffect="non-scaling-stroke" />
      {groupedLayer.map((m) => {
        const text = describeLayer(m);
        return (
          <line
            key={`p:${m.pos}:${m.direction}`}
            className={`xr-tick layer ${m.direction}`}
            x1={m.pos + 0.5}
            x2={m.pos + 0.5}
            y1={8}
            y2={m.direction === "up" ? 4 : 12}
            vectorEffect="non-scaling-stroke"
            onMouseEnter={() => onDetail(`${rowLabel} · ${text}`)}
            onMouseLeave={() => onDetail(null)}
          >
            <title>{text}</title>
          </line>
        );
      })}
      {groupedMarks.map((m) => {
        const text = describeMark(m);
        const y2 = m.direction === "golden" ? (m.count > 1 ? 1.5 : 1) : m.count > 1 ? 14.5 : 15;
        return (
          <g
            key={`x:${m.pos}:${m.direction}`}
            onMouseEnter={() => onDetail(`${rowLabel} · ${text}`)}
            onMouseLeave={() => onDetail(null)}
          >
            <line
              className={`xr-tick ${m.direction}${m.count > 1 ? " stacked" : ""}`}
              x1={m.pos + 0.5}
              x2={m.pos + 0.5}
              y1={8}
              y2={y2}
              vectorEffect="non-scaling-stroke"
            >
              <title>{text}</title>
            </line>
            {m.count > 1 && (
              <ellipse
                className={`xr-stack-dot ${m.direction}`}
                cx={m.pos + 0.5}
                cy={y2}
                rx={Math.max(0.45, n * 0.008)}
                ry={1.5}
              >
                <title>{text}</title>
              </ellipse>
            )}
          </g>
        );
      })}
    </svg>
  );
}

function PostureBar({ p }: { p: CrossPostureOut }) {
  const total = postureTotal(p);
  const title = POSTURES.map((s) => `${p[s.key]} ${s.label}`).join(" · ");
  return (
    <span className="xr-posture" title={title} aria-label={`posture: ${title}`}>
      {total > 0 &&
        POSTURES.filter((s) => p[s.key] > 0).map((s) => (
          <span key={s.key} className={`xr-seg ${s.key}`} style={{ flexGrow: p[s.key] }} />
        ))}
    </span>
  );
}

function crossWords(m: CrossTimelineNameOut, d: CrossRadarOut): string {
  if (m.flag != null) return leftOutWords(m.flag);
  if (!m.priced) {
    return m.not_priced === "stale_tape" && m.last_bar
      ? `not priced: last bar ${fmtDay(m.last_bar)}`
      : `not priced: ${plural(m.bars, "bar", "bars")}, a 200-day SMA needs 200`;
  }
  const parts = m.crosses.map((c) => `${c.direction} ${fmtDate(c.d)}`);
  if (d.price_200d) parts.push(...m.price_200d.map((c) => `price ${c.direction} through 200d ${fmtDate(c.d)}`));
  const shorter =
    m.readable_sessions < d.history_sessions ? ` (its tape reads the last ${m.readable_sessions} sessions)` : "";
  return parts.length === 0 ? `no cross in ${d.history_sessions} sessions${shorter}` : `${parts.join(" · ")}${shorter}`;
}

function Counts({ golden, death }: { golden: number; death: number }) {
  return (
    <span className="xr-counts">
      <span className={golden > 0 ? "xr-c golden" : "xr-c"}>↑ {golden}</span>
      <span className={death > 0 ? "xr-c death" : "xr-c"}>↓ {death}</span>
    </span>
  );
}

function TimelineRow({
  row,
  data,
  open,
  onToggle,
  onSelect,
  onDetail,
}: {
  row: CrossTimelineRowOut;
  data: CrossRadarOut;
  open: boolean;
  onToggle: () => void;
  onSelect: DashboardProps["onSelectThesis"];
  onDetail: (text: string | null) => void;
}) {
  const view = data.group_by;
  const label = groupLabel(row.group, view);
  const silent = view === "link" && (row.group.link === "Context" || row.group.link === "Bench");
  const marks = row.members.flatMap((m) => m.crosses.map((c) => ({ ...c, ticker: m.ticker })));
  const layer = row.members.flatMap((m) => m.price_200d.map((c) => ({ ...c, ticker: m.ticker })));
  const sessions = data.sessions.length;
  return (
    <>
      <tr
        className={`xr-tl${open ? " open" : ""}${silent ? " quiet" : ""}`}
        tabIndex={0}
        aria-expanded={open}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <td className="xr-label">
          <span className="xr-chev" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          {label}
          <span className="xr-sub">
            {plural(row.group.names, "name", "names")}
            {row.group.not_priced > 0 ? ` · ${row.group.not_priced} not priced` : ""}
            {row.group.flagged > 0 ? ` · ${row.group.flagged} left out` : ""}
          </span>
        </td>
        <td className="xr-num">
          <Counts golden={row.golden} death={row.death} />
        </td>
        <td className="xr-strip-cell">
          <Strip
            marks={marks}
            layer={layer}
            sessions={sessions}
            recentSessions={data.recent_sessions}
            rowLabel={label}
            label={`${label}: ${row.golden} golden, ${row.death} death`}
            onDetail={onDetail}
          />
        </td>
        <td>
          <PostureBar p={row.posture} />
        </td>
      </tr>
      {open &&
        row.members.map((m) => {
          const g = m.crosses.filter((c) => c.direction === "golden").length;
          const pInfo = POSTURES.find((p) => p.key === m.posture);
          return (
            <tr
              key={m.security_id}
              className={`xr-tl-name${m.priced ? "" : " unpriced"}${m.flag != null ? " flagged" : ""}`}
            >
              <td className="xr-name-id">
                <NameLink ticker={m.ticker} cards={m.cards} groupBy={view} onSelect={onSelect} />
                {m.rs_leading && <span className="xr-tag xr-rs">RS</span>}
              </td>
              <td className="xr-num">{m.priced ? <Counts golden={g} death={m.crosses.length - g} /> : null}</td>
              <td className="xr-strip-cell">
                {m.priced && (
                  <Strip
                    marks={m.crosses.map((c) => ({ ...c, ticker: m.ticker }))}
                    layer={m.price_200d.map((c) => ({ ...c, ticker: m.ticker }))}
                    sessions={sessions}
                    recentSessions={data.recent_sessions}
                    rowLabel={m.ticker}
                    label={`${m.ticker} crosses`}
                    onDetail={onDetail}
                  />
                )}
              </td>
              <td className="xr-v xr-dates">
                <span className="xr-member-meta">
                  {m.priced && pInfo && (
                    <span className={`xr-pdot ${m.posture}`} title={pInfo.label} aria-hidden="true" />
                  )}
                  {m.priced && m.gap_pct != null && (
                    <span className="xr-member-gap" title="50d vs 200d gap at as-of">
                      {fmtGap(m.gap_pct)}
                    </span>
                  )}
                  <span>{crossWords(m, data)}</span>
                </span>
              </td>
            </tr>
          );
        })}
    </>
  );
}

function Timeline({
  data,
  query,
  open,
  onToggle,
  onSelect,
}: {
  data: CrossRadarOut;
  query: string;
  open: ReadonlySet<string>;
  onToggle: (key: string) => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const [sort, setSort] = useState<Sort<TimelineCol>>(TIMELINE_SORT);
  const [detail, setDetail] = useState<string | null>(null);
  const view = data.group_by;
  const rows = useMemo(() => {
    const sorted = sortTimeline(data.timeline, sort, view);
    if (!query) return sorted;
    return sorted.filter(
      (r) =>
        groupLabel(r.group, view).toLocaleLowerCase().includes(query) ||
        r.members.some((m) => m.ticker.toLocaleLowerCase().includes(query)),
    );
  }, [data.timeline, sort, view, query]);
  const onSort = (c: TimelineCol) => setSort((s) => nextSort(s, c, timelineFirstDir));
  const first = data.sessions[0];
  const last = data.sessions[data.sessions.length - 1];
  return (
    <Section
      id="xr-timeline"
      title="Cross timeline"
      count={rows.length}
      rule={`Every golden (↑) and death (↓) cross of the last ${data.history_sessions} sessions, one row per group, the newest session at the right (shaded band = last ${data.recent_sessions} sessions)${data.price_200d ? "; the lighter ticks are price crossing the 200d (timeline only, never counted above)" : ""}. Beside each row, its names by 50d / 200d posture. Click a row for its names.`}
    >
      {rows.length === 0 ? (
        <div className="dash-note xr-empty">No companies in this view.</div>
      ) : (
        <>
          <div className="xr-tl-bar">
            <div className="xr-posture-legend" aria-hidden="true">
              {POSTURES.map((s) => (
                <span key={s.key} className="xr-pl-item">
                  <span className={`xr-pl-sw ${s.key}`} />
                  {s.key === "above_rising"
                    ? "50>200 rising"
                    : s.key === "above_falling"
                      ? "50>200 falling"
                      : s.key === "below_rising"
                        ? "50<200 rising"
                        : s.key === "below_falling"
                          ? "50<200 falling"
                          : "other"}
                </span>
              ))}
            </div>
            <div className="xr-tl-detail" data-testid="xr-tl-detail" aria-live="polite">
              {detail ?? "Hover any cross tick for ticker & date"}
            </div>
          </div>
          <div className="xr-scroll xr-scroll-tl">
            <table className="xr-table xr-timeline" aria-label="Cross timeline">
              <thead>
                <tr>
                  <SortTh col="label" label="Group" title="Sort by name" sort={sort} onSort={onSort} num={false} />
                  <th
                    className="xr-num"
                    aria-sort={
                      sort.col === "total" || sort.col === "golden" || sort.col === "death"
                        ? sort.dir === "asc"
                          ? "ascending"
                          : "descending"
                        : "none"
                    }
                  >
                    <span className="xr-cross-sorts">
                      <button
                        type="button"
                        className={`xr-sort${sort.col === "total" ? " active" : ""}`}
                        title="Sort by total crosses"
                        onClick={() => onSort("total")}
                      >
                        Crosses
                        {sort.col === "total" && (
                          <span className="xr-arrow" aria-hidden="true">
                            {sort.dir === "asc" ? "▲" : "▼"}
                          </span>
                        )}
                      </button>
                      <button
                        type="button"
                        className={`xr-sort xr-subsort${sort.col === "golden" ? " active" : ""}`}
                        title="Sort by golden crosses"
                        onClick={() => onSort("golden")}
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className={`xr-sort xr-subsort${sort.col === "death" ? " active" : ""}`}
                        title="Sort by death crosses"
                        onClick={() => onSort("death")}
                      >
                        ↓
                      </button>
                    </span>
                  </th>
                  <th className="xr-axis">
                    {first && last ? (
                      <span className="xr-axis-ends">
                        <span>{fmtDate(first)}</span>
                        <span>{fmtDate(last)}</span>
                      </span>
                    ) : null}
                  </th>
                  <th>Posture</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <TimelineRow
                    key={r.group.key}
                    row={r}
                    data={data}
                    open={open.has(r.group.key)}
                    onToggle={() => onToggle(r.group.key)}
                    onSelect={onSelect}
                    onDetail={setDetail}
                  />
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Section>
  );
}

export default function CrossRadar({ asof, onSelectThesis }: DashboardProps) {
  const [groupBy, setGroupBy] = useState<GroupBy>("link");
  const [includeSilent, setIncludeSilent] = useState(false);
  const [price200d, setPrice200d] = useState(false);
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const q = useCrossRadar(asof, groupBy, includeSilent, price200d);
  const data = q.data;

  if (q.isLoading) {
    return (
      <div className="dash-note">Computing across all cards — the first load after a run can take a minute.</div>
    );
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Cross Radar.</div>;

  const query = search.trim().toLocaleLowerCase();
  const nearCount = data.near_cross.reduce((acc, g) => acc + g.names.length, 0);
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="xr">
      <div className="xr-controls">
        <div className="xr-seg-group" role="group" aria-label="Group by">
          <span className="xr-seg-label">Group by</span>
          {GROUP_BY_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`xr-seg-btn${groupBy === o.value ? " on" : ""}`}
              aria-pressed={groupBy === o.value}
              onClick={() => {
                setGroupBy(o.value);
                setOpen(new Set());
              }}
            >
              {o.label}
            </button>
          ))}
        </div>
        <label className="xr-check" title="Add Context and Bench memberships (their own groups in the Link view)">
          <input type="checkbox" checked={includeSilent} onChange={(e) => setIncludeSilent(e.target.checked)} />
          Show Context and Bench
        </label>
        <label
          className="xr-check"
          title="Add price-crosses-the-200d ticks to the timeline (never counted in a cluster or a first mover)"
        >
          <input type="checkbox" checked={price200d} onChange={(e) => setPrice200d(e.target.checked)} />
          Price × 200d ticks
        </label>
        <input
          type="search"
          className="xr-search"
          aria-label="Filter groups or tickers"
          placeholder="Filter groups or tickers…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {q.isFetching && (
          <span className="dash-note">
            {data.asof !== asof
              ? "Computing across all cards at the new as-of — the first load after a run can take a minute."
              : "updating…"}
          </span>
        )}
      </div>

      <nav className="xr-jump" aria-label="Cross Radar sections">
        <a href="#xr-clusters" className={data.clusters.length > 0 ? "has" : undefined}>
          Clusters ({data.clusters.length})
        </a>
        <a href="#xr-movers" className={data.first_movers.length > 0 ? "has" : undefined}>
          First movers ({data.first_movers.length})
        </a>
        <a href="#xr-near" className={nearCount > 0 ? "has" : undefined}>
          Near-cross ({nearCount})
        </a>
        <a href="#xr-timeline">Timeline ({data.timeline.length})</a>
      </nav>

      <Clusters data={data} query={query} onSelect={onSelectThesis} />
      <FirstMovers data={data} query={query} onSelect={onSelectThesis} />
      <NearCross data={data} query={query} onSelect={onSelectThesis} />
      <Timeline data={data} query={query} open={open} onToggle={toggle} onSelect={onSelectThesis} />

      <div className="xr-foot">
        <div className="dash-note xr-work">{workLine(data)}</div>
        <FlaggedNote flagged={data.flagged} />
        {data.roster_fallback_cards > 0 && (
          <div className="dash-note xr-work">
            Today's roster stands in for {plural(data.roster_fallback_cards, "card", "cards")}: rosters are
            point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
          </div>
        )}
        <div className="dash-note xr-legend">
          A cross is the 50-day SMA crossing the 200-day: golden above, death below. Sessions are each company's own
          trading bars, counted back from its last bar; a name counts by its latest cross in the window. Priced = a
          200-day SMA at its last bar, that bar within {data.stale_gap_days} days of the as-of; the rest are counted as
          not priced, never as "no cross". SMA crosses lag by construction.
        </div>
      </div>
    </div>
  );
}
