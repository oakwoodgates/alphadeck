import { useMemo, useState } from "react";

import { FlaggedNote, leftOutWords } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./landing.css";
import {
  DEFAULT_SORT,
  GROUP_BY_OPTIONS,
  QUADRANTS,
  WINDOWS,
  fmtDay,
  fmtPos,
  fmtRatio,
  fmtRet,
  groupLabel,
  groupWindow,
  nameWindow,
  nextSort,
  quadrantOf,
  sortGroups,
  sortNames,
  splitParts,
  type SortCol,
  type SortState,
  type WindowKey,
} from "./landingView";
import {
  useLanding,
  type GroupBy,
  type LandingGroupOut,
  type LandingNameOut,
  type LandingOut,
  type LandingSplitOut,
} from "./useLanding";

// Price & Volume Landing (docs/dashboards/landing.md): where price closed in each window's range, and whether
// volume backed the move, by link, business type, sector or card, at the as-of. Display only. Every number
// (the medians, the splits, who counts) is computed server-side; this page picks the view, orders the rows
// and words the numbers.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";

const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

function cacheWords(d: LandingOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

function pricedWords(d: LandingOut): string {
  const out: string[] = [];
  if (d.no_tape_companies > 0) out.push(`${d.no_tape_companies.toLocaleString("en-US")} without a bar`);
  if (d.stale_tape_companies > 0)
    out.push(
      `${d.stale_tape_companies.toLocaleString("en-US")} stale: last bar over ${d.stale_gap_days} days before the as-of`,
    );
  const priced = `${d.priced_companies.toLocaleString("en-US")} priced`;
  return out.length ? `${priced} (${out.join(", ")})` : priced;
}

/** The quiet line that shows the work, every number off the payload. */
function workLine(d: LandingOut, groups: number): string {
  const cards =
    d.cards_not_yet_created > 0
      ? `${plural(d.cards_counted, "live card", "live cards")} counted (${d.cards_not_yet_created} created after the as-of left out)`
      : `${plural(d.cards_counted, "live card", "live cards")} counted`;
  return [
    cards,
    plural(d.companies, "company", "companies"),
    plural(groups, "group", "groups"),
    pricedWords(d),
    d.include_silent
      ? "Context and Bench included"
      : `Context and Bench left out (${plural(d.silent_rows_left_out, "basket row", "basket rows")})`,
    `${plural(d.unresolved_rows, "basket row", "basket rows")} without a resolved security not counted`,
    `volume vs the prior window of the same length; >${d.rising_mult}× = rising`,
    "windows in trading bars",
    cacheWords(d),
    `as of ${fmtDay(d.asof)}`,
  ].join(" · ");
}

/** A thin 0–100 bar: the fill runs from the low (0%) to the position. Nothing drawn for "—". */
function RangeBar({ value }: { value: number | null | undefined }) {
  return (
    <span className="ld-bar" aria-hidden="true">
      {value != null && <span className="ld-bar-fill" style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />}
    </span>
  );
}

function SplitLine({ split }: { split: LandingSplitOut }) {
  const parts = splitParts(split);
  const title = [
    ...QUADRANTS.map((q) => `${split[q.key]} ${q.full}`),
    `${split.unread} without a reading`,
  ].join("\n");
  return (
    <span className="ld-split" title={title}>
      {parts.length === 0
        ? "—"
        : parts.map((p, i) => (
            <span key={p.key}>
              {i > 0 && <span className="ld-dot"> · </span>}
              <span className={`ld-q${p.toned ? ` ${p.key}` : ""}`}>
                {p.n} {p.short}
              </span>
            </span>
          ))}
    </span>
  );
}

function WindowTh({
  win,
  label,
  bars,
  sort,
  onSort,
}: {
  win: WindowKey;
  label: string;
  bars: number;
  sort: SortState;
  onSort: (c: SortCol) => void;
}) {
  const pos: SortCol = `${win}:position`;
  const acc: SortCol = `${win}:accumulation`;
  const active = sort.col === pos || sort.col === acc ? sort.dir : null;
  const btn = (col: SortCol, text: string, name: string, title: string) => {
    const on = sort.col === col;
    return (
      <button
        type="button"
        className={`ld-sort${on ? " active" : ""}`}
        aria-label={name}
        title={title}
        onClick={() => onSort(col)}
      >
        {text}
        {on && (
          <span className="ld-arrow" aria-hidden="true">
            {sort.dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    );
  };
  return (
    <th
      className="ld-win"
      aria-sort={active === "asc" ? "ascending" : active === "desc" ? "descending" : "none"}
    >
      <span className="ld-win-label" title={`${bars} trading ${bars === 1 ? "bar" : "bars"}`}>
        {label}
      </span>
      <span className="ld-win-sorts">
        {btn(pos, "position", `${label} position`, `Sort by the median ${label} range position`)}
        {btn(acc, "accum", `${label} accumulation`, `Sort by names in accumulation over ${label}`)}
      </span>
    </th>
  );
}

function LabelTh({
  col,
  label,
  title,
  sort,
  onSort,
  num = false,
}: {
  col: "label" | "names";
  label: string;
  title: string;
  sort: SortState;
  onSort: (c: SortCol) => void;
  num?: boolean;
}) {
  const dir = sort.col === col ? sort.dir : null;
  return (
    <th
      className={num ? "ld-num" : undefined}
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
    >
      <button
        type="button"
        className={`ld-sort${dir ? " active" : ""}`}
        title={title}
        onClick={() => onSort(col)}
      >
        {label}
        {dir && (
          <span className="ld-arrow" aria-hidden="true">
            {dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    </th>
  );
}

function notPricedWords(m: LandingNameOut): string | null {
  if (m.flag != null) return leftOutWords(m.flag);
  if (m.priced) return null;
  if (m.not_priced === "stale_tape")
    return m.last_bar ? `stale tape · last bar ${fmtDay(m.last_bar)}` : "stale tape";
  return "no tape";
}

function NameRow({
  name,
  groupBy,
  onSelect,
}: {
  name: LandingNameOut;
  groupBy: GroupBy;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const off = notPricedWords(name);
  return (
    <tr className={`ld-name${name.flag != null ? " flagged" : ""}`}>
      <td className="ld-name-id">
        <span className="ld-tk">{name.ticker}</span>
        <span className="ld-chips">
          {name.cards.map((c) => {
            const links = c.links.map((l) => l ?? "Unsegmented").join(", ");
            return (
              <button
                key={c.thesis_id}
                type="button"
                className="ld-chip"
                title={`Open ${c.thesis_name} on ${c.ticker}`}
                onClick={() => onSelect(c.thesis_id, c.ticker)}
              >
                {c.thesis_name}
                {groupBy !== "link" && links && <span className="ld-chip-links"> · {links}</span>}
              </button>
            );
          })}
        </span>
        {off && <span className="ld-off">{off}</span>}
      </td>
      <td />
      {WINDOWS.map((w) => {
        const r = nameWindow(name, w.key);
        const q = r?.quadrant ? quadrantOf(r.quadrant) : null;
        const title = [
          `return ${fmtRet(r?.ret)}`,
          `volume ${fmtRatio(r?.vol_ratio)} the prior ${w.label}`,
          `up/down-day volume ${fmtRatio(r?.up_down)}`,
        ].join(" · ");
        return (
          <td key={w.key} className="ld-cell" title={title}>
            <span className="ld-pos">
              <RangeBar value={r?.range_pos} />
              <span className="ld-v">{fmtPos(r?.range_pos)}</span>
            </span>
            <span className="ld-sub">
              {q ? (
                <span className={`ld-chip-q${q.toned ? ` ${q.key}` : ""}`} title={q.full}>
                  {q.short}
                </span>
              ) : (
                <span className="ld-chip-q">—</span>
              )}{" "}
              vol {fmtRatio(r?.vol_ratio)}
            </span>
          </td>
        );
      })}
    </tr>
  );
}

function GroupRow({
  g,
  groupBy,
  open,
  sort,
  minPriced,
  onToggle,
  onSelect,
}: {
  g: LandingGroupOut;
  groupBy: GroupBy;
  open: boolean;
  sort: SortState;
  minPriced: number;
  onToggle: () => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const label = groupLabel(g, groupBy);
  const silent = groupBy === "link" && (g.link === "Context" || g.link === "Bench");
  return (
    <>
      <tr
        className={`ld-group${open ? " open" : ""}${silent ? " quiet" : ""}`}
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
        <td className="ld-label">
          <span className="ld-chev" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          {label}
        </td>
        <td className="ld-num">
          <span className="ld-v">{g.names}</span>
          {g.flagged > 0 && (
            <span className="ld-sub" title="left out of every number: a flagged price history">
              {g.flagged} left out
            </span>
          )}
        </td>
        {WINDOWS.map((w) => {
          const s = groupWindow(g, w.key);
          if (!s) return <td key={w.key} className="ld-cell" />;
          return (
            <td
              key={w.key}
              className="ld-cell"
              title={`median of ${s.range_n} ${s.range_n === 1 ? "name" : "names"} with a ${w.label} range position (fewer than ${minPriced} shows —)`}
            >
              <span className="ld-pos">
                <RangeBar value={s.range_median} />
                <span className="ld-v">{fmtPos(s.range_median)}</span>
                <span className="ld-n">n {s.range_n}</span>
              </span>
              <SplitLine split={s.split} />
              <span className="ld-sub">
                up/down {fmtRatio(s.up_down_median)} <span className="ld-n">n {s.up_down_n}</span>
              </span>
            </td>
          );
        })}
      </tr>
      {open &&
        sortNames(g.members, sort).map((m) => (
          <NameRow key={m.security_id} name={m} groupBy={groupBy} onSelect={onSelect} />
        ))}
    </>
  );
}

export default function Landing({ asof, onSelectThesis }: DashboardProps) {
  const [groupBy, setGroupBy] = useState<GroupBy>("link");
  const [includeSilent, setIncludeSilent] = useState(false);
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const q = useLanding(asof, groupBy, includeSilent);
  const data = q.data;

  const groups = useMemo(() => (data ? sortGroups(data.groups, sort, data.group_by) : []), [data, sort]);

  if (q.isLoading) {
    return (
      <div className="dash-note">
        Computing across all cards — the first load after a run can take a minute.
      </div>
    );
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Price & Volume Landing.</div>;

  const view = data.group_by;
  const bars = (key: string) => data.windows.find((w) => w.key === key)?.bars ?? 0;
  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const onSort = (col: SortCol) => setSort((s) => nextSort(s, col));

  return (
    <div className="ld">
      <div className="ld-controls">
        <div className="ld-seg" role="group" aria-label="Group by">
          <span className="ld-seg-label">Group by</span>
          {GROUP_BY_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`ld-seg-btn${groupBy === o.value ? " on" : ""}`}
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
        <label className="ld-check" title="Add Context and Bench memberships (their own rows in the Link view)">
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
      <div className="dash-note ld-work">{workLine(data, groups.length)}</div>
      <FlaggedNote flagged={data.flagged} />
      {data.roster_fallback_cards > 0 && (
        <div className="dash-note ld-work">
          Today's roster stands in for {plural(data.roster_fallback_cards, "card", "cards")}: rosters are
          point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
        </div>
      )}
      <div className="dash-note ld-legend">
        Position is where the close sits in the window's high–low range (0% at the low, 100% at the high): the
        median of the group's names with one, fewer than {data.min_priced} shows —. The split counts names by
        the window's return and volume: accum = up on rising volume · thin = up, volume not rising · dist =
        down on rising volume · rest = down, volume not rising · flat = unchanged. Up/down is the volume on
        up-close days over the volume on down-close days (median). Click a row for its names.
      </div>

      {groups.length === 0 ? (
        <div className="dash-note">No companies in this view.</div>
      ) : (
        <div className="ld-scroll">
          <table className="ld-table">
            <thead>
              <tr>
                <LabelTh
                  col="label"
                  label={GROUP_BY_OPTIONS.find((o) => o.value === view)?.label ?? "Group"}
                  title="Sort by name"
                  sort={sort}
                  onSort={onSort}
                />
                <LabelTh
                  col="names"
                  label="Names"
                  title="Sort by companies in the group"
                  sort={sort}
                  onSort={onSort}
                  num
                />
                {WINDOWS.map((w) => (
                  <WindowTh
                    key={w.key}
                    win={w.key}
                    label={w.label}
                    bars={bars(w.key)}
                    sort={sort}
                    onSort={onSort}
                  />
                ))}
              </tr>
            </thead>
            <tbody>
              {groups.map((g) => (
                <GroupRow
                  key={g.key}
                  g={g}
                  groupBy={view}
                  open={open.has(g.key)}
                  sort={sort}
                  minPriced={data.min_priced}
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
