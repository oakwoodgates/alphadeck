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

function Section({ title, rule, children }: { title: string; rule: string; children: ReactNode }) {
  return (
    <section className="xr-sec" aria-label={title}>
      <h2 className="xr-h">{title}</h2>
      <div className="dash-note xr-rule">{rule}</div>
      {children}
    </section>
  );
}

// --- 1. clusters (loud: exceptions only) --------------------------------------------------------------

function Clusters({
  data,
  onSelect,
}: {
  data: CrossRadarOut;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const [sort, setSort] = useState<Sort<ClusterCol>>(CLUSTER_SORT);
  const view = data.group_by;
  const rows = useMemo(() => sortClusters(data.clusters, sort, view), [data.clusters, sort, view]);
  const onSort = (c: ClusterCol) => setSort((s) => nextSort(s, c, clusterFirstDir));
  return (
    <Section
      title="Clusters"
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
  onSelect,
}: {
  data: CrossRadarOut;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const [sort, setSort] = useState<Sort<MoverCol>>(MOVER_SORT);
  const view = data.group_by;
  const rows = useMemo(() => sortMovers(data.first_movers, sort, view), [data.first_movers, sort, view]);
  const onSort = (c: MoverCol) => setSort((s) => nextSort(s, c, moverFirstDir));
  return (
    <Section
      title="First movers"
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
  onSelect,
}: {
  data: CrossRadarOut;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const [sort, setSort] = useState<Sort<NearCol>>(NEAR_SORT);
  const view = data.group_by;
  const groups = useMemo(() => sortNear(data.near_cross, sort, view), [data.near_cross, sort, view]);
  const onSort = (c: NearCol) => setSort((s) => nextSort(s, c, nearFirstDir));
  return (
    <Section
      title="Near-cross watch"
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
                <NearGroupRows key={g.group.key} g={g} view={view} onSelect={onSelect} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

function NearGroupRows({
  g,
  view,
  onSelect,
}: {
  g: CrossRadarOut["near_cross"][number];
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
          <td className="xr-v">approaching {n.toward}</td>
          <td className="xr-num xr-v">{fmtGap(n.gap_pct)}</td>
          <td className="xr-num xr-v" title={`${fmtGap(n.gap_pct_then)} → ${fmtGap(n.gap_pct)}`}>
            {fmtPts(n.change)}
          </td>
        </tr>
      ))}
    </>
  );
}

// --- 4 + 5. the cross timeline and the posture bar ------------------------------------------------------

function Strip({
  marks,
  layer,
  sessions,
  label,
}: {
  marks: readonly CrossMarkOut[];
  layer: readonly CrossMarkOut[];
  sessions: number;
  label: string;
}) {
  const n = Math.max(sessions, 1);
  return (
    <svg
      className="xr-strip"
      viewBox={`0 0 ${n} 14`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
    >
      <line className="xr-base" x1={0} x2={n} y1={7} y2={7} vectorEffect="non-scaling-stroke" />
      {layer.map((m, i) => (
        <line
          key={`p${i}`}
          className={`xr-tick layer ${m.direction}`}
          x1={m.pos + 0.5}
          x2={m.pos + 0.5}
          y1={7}
          y2={m.direction === "up" ? 3.5 : 10.5}
          vectorEffect="non-scaling-stroke"
        >
          <title>{`price crossed ${m.direction === "up" ? "above" : "below"} the 200d on ${fmtDate(m.d)}`}</title>
        </line>
      ))}
      {marks.map((m, i) => (
        <line
          key={`x${i}`}
          className={`xr-tick ${m.direction}`}
          x1={m.pos + 0.5}
          x2={m.pos + 0.5}
          y1={7}
          y2={m.direction === "golden" ? 0.5 : 13.5}
          vectorEffect="non-scaling-stroke"
        >
          <title>{`${m.direction} cross on ${fmtDate(m.d)} (${sessionsAgo(m.sessions_ago)})`}</title>
        </line>
      ))}
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
}: {
  row: CrossTimelineRowOut;
  data: CrossRadarOut;
  open: boolean;
  onToggle: () => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const view = data.group_by;
  const label = groupLabel(row.group, view);
  const silent = view === "link" && (row.group.link === "Context" || row.group.link === "Bench");
  const marks = row.members.flatMap((m) => m.crosses);
  const layer = row.members.flatMap((m) => m.price_200d);
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
          <Strip marks={marks} layer={layer} sessions={sessions} label={`${label}: ${row.golden} golden, ${row.death} death`} />
        </td>
        <td>
          <PostureBar p={row.posture} />
        </td>
      </tr>
      {open &&
        row.members.map((m) => {
          const g = m.crosses.filter((c) => c.direction === "golden").length;
          return (
            <tr
              key={m.security_id}
              className={`xr-tl-name${m.priced ? "" : " unpriced"}${m.flag != null ? " flagged" : ""}`}
            >
              <td className="xr-name-id">
                <NameLink ticker={m.ticker} cards={m.cards} groupBy={view} onSelect={onSelect} />
              </td>
              <td className="xr-num">{m.priced ? <Counts golden={g} death={m.crosses.length - g} /> : null}</td>
              <td className="xr-strip-cell">
                {m.priced && (
                  <Strip marks={m.crosses} layer={m.price_200d} sessions={sessions} label={`${m.ticker} crosses`} />
                )}
              </td>
              <td className="xr-v xr-dates">{crossWords(m, data)}</td>
            </tr>
          );
        })}
    </>
  );
}

function Timeline({
  data,
  open,
  onToggle,
  onSelect,
}: {
  data: CrossRadarOut;
  open: ReadonlySet<string>;
  onToggle: (key: string) => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const [sort, setSort] = useState<Sort<TimelineCol>>(TIMELINE_SORT);
  const view = data.group_by;
  const rows = useMemo(() => sortTimeline(data.timeline, sort, view), [data.timeline, sort, view]);
  const onSort = (c: TimelineCol) => setSort((s) => nextSort(s, c, timelineFirstDir));
  const first = data.sessions[0];
  const last = data.sessions[data.sessions.length - 1];
  return (
    <Section
      title="Cross timeline"
      rule={`Every golden (↑) and death (↓) cross of the last ${data.history_sessions} sessions, one row per group, the newest session at the right${data.price_200d ? "; the lighter ticks are price crossing the 200d (timeline only, never counted above)" : ""}. Beside each row, its names by 50d / 200d posture. Click a row for its names.`}
    >
      {rows.length === 0 ? (
        <div className="dash-note xr-empty">No companies in this view.</div>
      ) : (
        <div className="xr-scroll">
          <table className="xr-table xr-timeline" aria-label="Cross timeline">
            <thead>
              <tr>
                <SortTh col="label" label="Group" title="Sort by name" sort={sort} onSort={onSort} num={false} />
                <SortTh col="total" label="Crosses" title="Sort by total crosses" sort={sort} onSort={onSort} />
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
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Section>
  );
}

export default function CrossRadar({ asof, onSelectThesis }: DashboardProps) {
  const [groupBy, setGroupBy] = useState<GroupBy>("link");
  const [includeSilent, setIncludeSilent] = useState(false);
  const [price200d, setPrice200d] = useState(false);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const q = useCrossRadar(asof, groupBy, includeSilent, price200d);
  const data = q.data;

  if (q.isLoading) {
    return (
      <div className="dash-note">Computing across all cards — the first load after a run can take a minute.</div>
    );
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Cross Radar.</div>;

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
        {q.isFetching && (
          <span className="dash-note">
            {data.asof !== asof
              ? "Computing across all cards at the new as-of — the first load after a run can take a minute."
              : "updating…"}
          </span>
        )}
      </div>
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

      <Clusters data={data} onSelect={onSelectThesis} />
      <FirstMovers data={data} onSelect={onSelectThesis} />
      <NearCross data={data} onSelect={onSelectThesis} />
      <Timeline data={data} open={open} onToggle={toggle} onSelect={onSelectThesis} />
    </div>
  );
}
