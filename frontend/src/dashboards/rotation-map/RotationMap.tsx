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
        <td className="rm-label">
          <span className="rm-chev" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          {label}
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
            </td>
          );
        })}
        <td className="rm-num rm-v">
          {g.above_50d_n === 0 ? "—" : `${g.above_50d}/${g.above_50d_n}`}
        </td>
        <td className="rm-num">
          <span className="rm-v">{fmtRatio(g.rvol20_median)}</span>
          <span className="rm-sub">n {g.rvol20_n}</span>
        </td>
        <td className="rm-num rm-v">{g.golden > 0 ? `↑ ${g.golden}` : "0"}</td>
        <td className="rm-num rm-v">{g.death > 0 ? `↓ ${g.death}` : "0"}</td>
        <td className="rm-num rm-v">{g.rs_n === 0 ? "—" : `${g.rs_leaders}/${g.rs_n}`}</td>
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
  const q = useRotationMap(asof, groupBy, includeSilent);
  const data = q.data;

  const groups = useMemo(
    () => (data ? sortGroups(data.groups, sort, data.group_by, data.min_priced) : []),
    [data, sort],
  );

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
        {q.isFetching && (
          <span className="dash-note">
            {data.asof !== asof
              ? "Computing across all cards at the new as-of — the first load after a run can take a minute."
              : "updating…"}
          </span>
        )}
      </div>
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

      {groups.length === 0 ? (
        <div className="dash-note">No companies in this view.</div>
      ) : (
        <div className="rm-scroll">
          <table className="rm-table">
            <thead>
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
    </div>
  );
}
