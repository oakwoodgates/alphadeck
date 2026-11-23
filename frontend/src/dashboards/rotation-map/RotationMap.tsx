import { useMemo, useState } from "react";

import { fmtDate } from "../../util/format";
import { FlaggedNote, leftOutWords } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./rotation-map.css";
import {
  DEFAULT_SORT,
  GROUP_BY_OPTIONS,
  WINDOWS,
  fmtDay,
  fmtPct,
  fmtRatio,
  groupLabel,
  nextSort,
  shown,
  sortGroups,
  sortNames,
  windowOf,
  type SortCol,
  type SortState,
} from "./rotationView";
import {
  useRotationMap,
  type GroupBy,
  type RotationGroupOut,
  type RotationMapOut,
  type RotationNameOut,
} from "./useRotationMap";

// Rotation Map (docs/dashboards/rotation-map.md): which links, business types, sectors or cards have price
// moving, across every live card, at the as-of. Display only. Every number (the medians, x/n, the 10% tones,
// who counts) is computed server-side; this page picks the view, orders the rows and shifts the returns by
// SPY's own return when asked.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";

const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

function cacheWords(d: RotationMapOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The quiet line that shows the work, every number off the payload. */
function workLine(d: RotationMapOut, groups: number, vsSpy: boolean): string {
  const cards =
    d.cards_not_yet_created > 0
      ? `${plural(d.cards_counted, "live card", "live cards")} counted (${d.cards_not_yet_created} created after the as-of left out)`
      : `${plural(d.cards_counted, "live card", "live cards")} counted`;
  return [
    cards,
    plural(d.companies, "company", "companies"),
    plural(groups, "group", "groups"),
    `${d.priced_companies.toLocaleString("en-US")} priced`,
    d.include_silent
      ? "Context and Bench included"
      : `Context and Bench left out (${plural(d.silent_rows_left_out, "basket row", "basket rows")})`,
    `${plural(d.unresolved_rows, "basket row", "basket rows")} without a resolved security not counted`,
    vsSpy ? "returns vs SPY" : "returns raw",
    cacheWords(d),
    `as of ${fmtDay(d.asof)}`,
  ].join(" · ");
}

function SortTh({
  col,
  label,
  title,
  sort,
  onSort,
  num = true,
}: {
  col: SortCol;
  label: string;
  title: string;
  sort: SortState;
  onSort: (c: SortCol) => void;
  num?: boolean;
}) {
  const dir = sort.col === col ? sort.dir : null;
  return (
    <th
      className={num ? "rm-num" : undefined}
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
    >
      <button
        type="button"
        className={`rm-sort${dir ? " active" : ""}`}
        title={title}
        onClick={() => onSort(col)}
      >
        {label}
        {dir && (
          <span className="rm-arrow" aria-hidden="true">
            {dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    </th>
  );
}

function NameRow({
  name,
  data,
  groupBy,
  vsSpy,
  onSelect,
}: {
  name: RotationNameOut;
  data: RotationMapOut;
  groupBy: GroupBy;
  vsSpy: boolean;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  return (
    <tr className={`rm-name${name.flag != null ? " flagged" : ""}`}>
      <td className="rm-name-id">
        <span className="rm-tk">{name.ticker}</span>
        <span className="rm-chips">
          {name.cards.map((c) => {
            const links = c.links.map((l) => l ?? "Unsegmented").join(", ");
            return (
              <button
                key={c.thesis_id}
                type="button"
                className="rm-chip"
                title={`Open ${c.thesis_name} on ${c.ticker}`}
                onClick={() => onSelect(c.thesis_id, c.ticker)}
              >
                {c.thesis_name}
                {groupBy !== "link" && links && <span className="rm-chip-links"> · {links}</span>}
              </button>
            );
          })}
        </span>
        {name.flag != null && <span className="rm-off">{leftOutWords(name.flag)}</span>}
      </td>
      <td />
      {WINDOWS.map((w) => (
        <td key={w.key} className="rm-num rm-v">
          {fmtPct(shown(name.returns[w.key], w.key, vsSpy, data.spy))}
        </td>
      ))}
      <td className="rm-num rm-v">
        {name.above_50d == null ? "—" : name.above_50d ? "yes" : "no"}
      </td>
      <td className="rm-num rm-v">{fmtRatio(name.rvol20)}</td>
      <td className="rm-num rm-v">
        {name.cross === "golden" ? (
          <span className="rm-x" title={`golden cross: 50d crossed above 200d on ${fmtDate(name.cross_date)}`}>
            ↑
          </span>
        ) : null}
      </td>
      <td className="rm-num rm-v">
        {name.cross === "death" ? (
          <span className="rm-x" title={`death cross: 50d crossed below 200d on ${fmtDate(name.cross_date)}`}>
            ↓
          </span>
        ) : null}
      </td>
      <td className="rm-num rm-v">
        {name.rs_leading ? (
          <span className="rm-x" title="relative strength at a 13-week high">
            ↑
          </span>
        ) : null}
      </td>
    </tr>
  );
}

/** Quiet monochrome proportional bar (0–100%) rendered under a ratio cell. */
function RatioBar({ num, den }: { num: number | null | undefined; den: number }) {
  if (num == null || den <= 0) return null;
  const pct = Math.max(0, Math.min(100, (num / den) * 100));
  return (
    <span className="rm-bar" aria-hidden="true">
      <span className="rm-bar-fill" style={{ width: `${pct}%` }} />
    </span>
  );
}

/** Pinned SPY reference row at the top of the table so group medians always have a live market anchor. */
function SpyRow({ spy, vsSpy }: { spy: NonNullable<RotationMapOut["spy"]>; vsSpy: boolean }) {
  return (
    <tr className="rm-spy-row" data-testid="rm-spy-row">
      <td className="rm-label rm-spy-label">
        <span className="rm-spy-tag">SPY</span>
        <span className="rm-spy-hint">{vsSpy ? "benchmark (0.0% baseline)" : "benchmark"}</span>
      </td>
      <td className="rm-num rm-v rm-zero">—</td>
      {WINDOWS.map((w) => {
        const raw = spy[w.key] ?? null;
        return (
          <td key={w.key} className="rm-num">
            <span className="rm-v">{vsSpy ? (raw == null ? "—" : "0.0%") : fmtPct(raw)}</span>
            {vsSpy && raw != null && <span className="rm-sub">SPY {fmtPct(raw)}</span>}
          </td>
        );
      })}
      <td className="rm-num rm-v rm-zero">—</td>
      <td className="rm-num rm-v rm-zero">—</td>
      <td className="rm-num rm-v rm-zero">—</td>
      <td className="rm-num rm-v rm-zero">—</td>
      <td className="rm-num rm-v rm-zero">—</td>
    </tr>
  );
}

function GroupRow({
  g,
  data,
  groupBy,
  vsSpy,
  open,
  sort,
  onToggle,
  onSelect,
}: {
  g: RotationGroupOut;
  data: RotationMapOut;
  groupBy: GroupBy;
  vsSpy: boolean;
  open: boolean;
  sort: SortState;
  onToggle: () => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const label = groupLabel(g, groupBy);
  const silent = groupBy === "link" && (g.link === "Context" || g.link === "Bench");
  const canOpenCard = (groupBy === "link" || groupBy === "card") && g.thesis_id != null;
  return (
    <>
      <tr
        className={`rm-group${open ? " open" : ""}${silent ? " quiet" : ""}`}
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
        <td className="rm-label" title={label}>
          <span className="rm-chev" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          <span className="rm-label-txt">{label}</span>
          {canOpenCard && (
            <button
              type="button"
              className="rm-open"
              aria-label={`Open ${g.thesis_name ?? "card"} in Cockpit`}
              title={`Open ${g.thesis_name ?? "card"} in Cockpit`}
              onClick={(e) => {
                e.stopPropagation();
                if (g.thesis_id) onSelect(g.thesis_id);
              }}
            >
              <svg width="11" height="11" viewBox="0 0 12 12" aria-hidden="true">
                <path d="M4.5 2.5H9.5V7.5M9.5 2.5L3 9" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          )}
        </td>
        <td className="rm-num">
          <span className="rm-v">{g.names}</span>
          {g.flagged > 0 && (
            <span className="rm-sub" title="left out of every number: a flagged price history">
              {g.flagged} left out
            </span>
          )}
        </td>
        {WINDOWS.map((w) => {
          const s = windowOf(g, w.key);
          const v = shown(s?.median, w.key, vsSpy, data.spy);
          const n = s?.n ?? 0;
          const x = vsSpy ? s?.above_spy : s?.up;
          return (
            <td key={w.key} className="rm-num">
              <span className={`rm-ret${v != null && s?.tone ? ` ${s.tone}` : ""}`}>
                {fmtPct(v)}
              </span>
              <span className="rm-sub">
                {x == null ? `n ${n}` : `${x}/${n} ${vsSpy ? "> SPY" : "up"}`}
              </span>
              <RatioBar num={x} den={n} />
            </td>
          );
        })}
        <td className="rm-num rm-v">
          <span>{g.above_50d_n === 0 ? "—" : `${g.above_50d}/${g.above_50d_n}`}</span>
          <RatioBar num={g.above_50d} den={g.above_50d_n} />
        </td>
        <td className="rm-num">
          <span className="rm-v">{fmtRatio(g.rvol20_median)}</span>
          <span className="rm-sub">n {g.rvol20_n}</span>
        </td>
        <td className={`rm-num rm-v${g.golden === 0 ? " rm-zero" : ""}`}>{g.golden > 0 ? `↑ ${g.golden}` : "—"}</td>
        <td className={`rm-num rm-v${g.death === 0 ? " rm-zero" : ""}`}>{g.death > 0 ? `↓ ${g.death}` : "—"}</td>
        <td className="rm-num rm-v">
          <span>{g.rs_n === 0 ? "—" : `${g.rs_leaders}/${g.rs_n}`}</span>
          <RatioBar num={g.rs_leaders} den={g.rs_n} />
        </td>
      </tr>
      {open &&
        sortNames(g.members, sort).map((m) => (
          <NameRow
            key={m.security_id}
            name={m}
            data={data}
            groupBy={groupBy}
            vsSpy={vsSpy}
            onSelect={onSelect}
          />
        ))}
    </>
  );
}

export default function RotationMap({ asof, onSelectThesis }: DashboardProps) {
  const [groupBy, setGroupBy] = useState<GroupBy>("link");
  const [includeSilent, setIncludeSilent] = useState(false);
  const [vsSpyPick, setVsSpy] = useState(false);
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [query, setQuery] = useState("");
  const q = useRotationMap(asof, groupBy, includeSilent);
  const data = q.data;

  const qNorm = query.trim().toLowerCase();
  const groups = useMemo(() => {
    if (!data) return [];
    const sorted = sortGroups(data.groups, sort, data.group_by, data.min_priced);
    if (!qNorm) return sorted;
    return sorted.filter(
      (g) =>
        groupLabel(g, data.group_by).toLowerCase().includes(qNorm) ||
        g.members.some((m) => m.ticker.toLowerCase().includes(qNorm)),
    );
  }, [data, sort, qNorm]);

  if (q.isLoading) {
    return (
      <div className="dash-note">
        Computing across all cards — the first load after a run can take a minute.
      </div>
    );
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Rotation Map.</div>;

  const view = data.group_by;
  const vsSpy = vsSpyPick && data.spy != null;
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const onSort = (col: SortCol) => setSort((s) => nextSort(s, col));

  return (
    <div className="rm">
      <div className="rm-controls">
        <div className="rm-seg" role="group" aria-label="Group by">
          <span className="rm-seg-label">Group by</span>
          {GROUP_BY_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`rm-seg-btn${groupBy === o.value ? " on" : ""}`}
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
        <div className="rm-seg" role="group" aria-label="Returns">
          <span className="rm-seg-label">Returns</span>
          <button
            type="button"
            className={`rm-seg-btn${!vsSpy ? " on" : ""}`}
            aria-pressed={!vsSpy}
            onClick={() => setVsSpy(false)}
          >
            Raw
          </button>
          <button
            type="button"
            className={`rm-seg-btn${vsSpy ? " on" : ""}`}
            aria-pressed={vsSpy}
            disabled={data.spy == null}
            title={
              data.spy == null
                ? "No SPY tape at this as-of"
                : "Each return minus SPY's own return over the same window"
            }
            onClick={() => setVsSpy(true)}
          >
            vs SPY
          </button>
          {data.spy == null && <span className="dash-note">no SPY tape at this as-of</span>}
        </div>
        <label className="rm-check" title="Add Context and Bench memberships (their own rows in the Link view)">
          <input
            type="checkbox"
            checked={includeSilent}
            onChange={(e) => setIncludeSilent(e.target.checked)}
          />
          Show Context and Bench
        </label>
        <input
          type="search"
          className="rm-search"
          aria-label="Filter group or ticker"
          placeholder="Find group or ticker…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        {q.isFetching && (
          <span className="dash-note">
            {data.asof !== asof
              ? "Computing across all cards at the new as-of — the first load after a run can take a minute."
              : "updating…"}
          </span>
        )}
      </div>

      {groups.length === 0 ? (
        <div className="dash-note">{qNorm ? "No group or ticker matches that filter." : "No companies in this view."}</div>
      ) : (
        <div className="rm-scroll">
          <table className="rm-table">
            <thead>
              <tr className="rm-super" aria-hidden="true">
                <td colSpan={2}>Group</td>
                <td colSpan={WINDOWS.length} className="rm-grp-start">
                  Median returns ({vsSpy ? "vs SPY" : "raw"})
                </td>
                <td colSpan={5} className="rm-grp-start">
                  Breadth, volume &amp; crosses
                </td>
              </tr>
              <tr>
                <SortTh
                  col="label"
                  label={GROUP_BY_OPTIONS.find((o) => o.value === view)?.label ?? "Group"}
                  title="Sort by name"
                  sort={sort}
                  onSort={onSort}
                  num={false}
                />
                <SortTh col="names" label="Names" title="Sort by companies in the group" sort={sort} onSort={onSort} />
                {WINDOWS.map((w) => (
                  <SortTh
                    key={w.key}
                    col={w.key}
                    label={`${w.label} median`}
                    title={`Sort by the median ${w.label} return`}
                    sort={sort}
                    onSort={onSort}
                  />
                ))}
                <SortTh
                  col="above_50d"
                  label="Above 50d"
                  title={`Sort by the share of names above their 50-day SMA (fewer than ${data.min_priced} names read sorts last)`}
                  sort={sort}
                  onSort={onSort}
                />
                <SortTh col="rvol20" label="RVOL 20D median" title="Sort by the median 20-day RVOL" sort={sort} onSort={onSort} />
                <SortTh
                  col="golden"
                  label={`Golden ${data.cross_days}d`}
                  title={`Sort by golden crosses in the last ${data.cross_days} days`}
                  sort={sort}
                  onSort={onSort}
                />
                <SortTh
                  col="death"
                  label={`Death ${data.cross_days}d`}
                  title={`Sort by death crosses in the last ${data.cross_days} days`}
                  sort={sort}
                  onSort={onSort}
                />
                <SortTh
                  col="rs_leaders"
                  label="RS leaders"
                  title="Sort by names with relative strength at a 13-week high"
                  sort={sort}
                  onSort={onSort}
                />
              </tr>
            </thead>
            <tbody>
              {data.spy != null && <SpyRow spy={data.spy} vsSpy={vsSpy} />}
              {groups.map((g) => (
                <GroupRow
                  key={g.key}
                  g={g}
                  data={data}
                  groupBy={view}
                  vsSpy={vsSpy}
                  open={open.has(g.key)}
                  sort={sort}
                  onToggle={() => toggle(g.key)}
                  onSelect={onSelectThesis}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="rm-foot">
        <div className="dash-note rm-work">{workLine(data, groups.length, vsSpy)}</div>
        <FlaggedNote flagged={data.flagged} />
        {data.roster_fallback_cards > 0 && (
          <div className="dash-note rm-work">
            Today's roster stands in for {plural(data.roster_fallback_cards, "card", "cards")}: rosters are
            point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
          </div>
        )}
        <div className="dash-note rm-legend">
          Each return is the median of the group's priced names, with how many are{" "}
          {vsSpy ? "above SPY" : "up"} under it; fewer than {data.min_priced} priced shows —. Green / red
          marks a median in the top / bottom 10% of the groups shown in that column; everything else stays
          gray. Crosses count a golden / death cross in the last {data.cross_days} days. Click a row for
          its names.
        </div>
      </div>
    </div>
  );
}
