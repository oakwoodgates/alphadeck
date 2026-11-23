import { useMemo, useState } from "react";

import { fmtDate, STATE_CLASS, STATE_LABEL } from "../../util/format";
import type { DashboardProps } from "../registry";
import "./trigger-log.css";
import {
  DAYS_OPTIONS,
  DEFAULT_DAYS,
  DEFAULT_FILTERS,
  DEFAULT_ROLE,
  DEFAULT_SORT,
  GRADE_LABEL,
  GROUP_BYS,
  LIMITS,
  NO_AGGREGATE,
  RECONSTRUCTED_TITLE,
  ROLE_GLYPH,
  ROLE_LABEL,
  ROLE_OPTIONS,
  cacheWords,
  dimValues,
  emptyWords,
  facetCounts,
  filterRows,
  filtersActive,
  fireNote,
  groupRows,
  kindWords,
  labelTitle,
  liveWords,
  nextSort,
  pct,
  plural,
  roleWords,
  sortGroupRows,
  sourceView,
  statePair,
  statePairWords,
  toggleCount,
  toggleDiscriminates,
  tone,
  turnsWords,
  weekLabel,
  workLines,
  type Dim,
  type Filters,
  type GradeKey,
  type GroupBy,
  type Role,
  type Row,
  type SortKey,
  type SortState,
  type Toggle,
} from "./logView";
import {
  useTriggerLog,
  type TriggerLogDays,
  type TriggerLogNameOut,
  type TriggerLogOut,
  type TriggerLogRole,
} from "./useTriggerLog";
import { ExternalLink } from "../../components/ExternalLink";
import { RecordStartNote } from "../RecordStartNote";

// Trigger Log (docs/dashboards/trigger-log.md): every entry trigger and risk signal the RECORDED calls show firing
// across the cards in a window, one row per trigger (the same trigger on twelve nights is one row, dated the
// first night the record shows it), with its source, the card's state that night beside the night before, whether
// it is still live, and what the name's tape did since. Display only, and no aggregate: the Track Record page
// slices outcomes by market-cap tier. Every row and count is computed server-side; this page groups (week, kind,
// card), filters and words them. The trading set's tape look (docs/DASHBOARDS.md §Looks): dense rows,
// mono figures, one accent, the sign tone only on the signed since-values.

const SLOW = "Reading the recorded calls — the first load after a run or a basket edit can take a moment.";

const COLUMNS: readonly { key: string; label: string; sortKey?: SortKey }[] = [
  { key: "night", label: "recorded", sortKey: "night" },
  { key: "fired", label: "fired", sortKey: "fired" },
  { key: "tk", label: "ticker", sortKey: "tk" },
  { key: "card", label: "card" },
  { key: "kind", label: "kind" },
  { key: "turns", label: "turns" },
  { key: "grade", label: "grade" },
  { key: "label", label: "label" },
  { key: "src", label: "source" },
  { key: "state", label: "state that night" },
  { key: "live", label: "live" },
  { key: "sess", label: "sessions", sortKey: "sess" },
  { key: "since", label: "since · IWM", sortKey: "since" },
];

function StateCell({ row }: { row: Row }) {
  const p = statePair(row);
  const word = (s: string) => (
    <span className={`tl-st ${STATE_CLASS[s] ?? ""}`}>{STATE_LABEL[s] ?? s}</span>
  );
  return (
    <div role="cell" className="tl-c tl-c-state" data-label="state" aria-label={statePairWords(p)}>
      {p.kind === "new" && (
        <>
          <span className="tl-quiet">new card</span> {word(p.to)}
        </>
      )}
      {p.kind === "moved" && (
        <>
          {word(p.from)} <span className="tl-arrow">→</span> {word(p.to)}
        </>
      )}
      {p.kind === "same" && word(p.to)}
      {p.kind === "unknown-before" && (
        <>
          <span className="tl-quiet" title="the row before this night could not be read">
            ?
          </span>{" "}
          <span className="tl-arrow">→</span> {word(p.to)}
        </>
      )}
    </div>
  );
}

function SinceCell({ row }: { row: Row }) {
  const s = row.since;
  if (s.status === "flagged") {
    return (
      <div role="cell" className="tl-c tl-c-since" title={s.reason ?? undefined}>
        <span className="tl-quiet">flagged: {s.reason}</span>
      </div>
    );
  }
  if (s.status === "unpriced") {
    return (
      <div role="cell" className="tl-c tl-c-since">
        <span className="tl-quiet">unpriced</span>
      </div>
    );
  }
  return (
    <div
      role="cell"
      className="tl-c tl-c-since"
      title={`as-traded close ${fmtDate(s.entry_date)} → ${fmtDate(s.end_date)}`}
    >
      <span className={`tl-ret ${tone(s.ret)}`}>{pct(s.ret)}</span>
      {s.bench_ret != null && (
        <span className="tl-bench" title={`IWM over the same two dates, ${fmtDate(s.entry_date)} → ${fmtDate(s.end_date)}`}>
          {" "}
          IWM {pct(s.bench_ret)}
        </span>
      )}
    </div>
  );
}

function TriggerRow({
  row,
  expanded,
  onToggleExpand,
  onSelectThesis,
}: {
  row: Row;
  expanded: boolean;
  onToggleExpand: () => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const note = fireNote(row);
  const grade = row.grade;
  const sourceDetails = row.sources.filter((s) => s.detail.length > 0);
  const hasDetails = row.label_changes > 0 || sourceDetails.length > 0;
  return (
    <>
      <div role="row" className={`tl-row${row.newest ? " newest" : ""}`} data-testid="tl-row" data-role={row.role}>
        <div role="cell" className="tl-c tl-c-night" title={row.recorded_on}>
          {fmtDate(row.recorded_on)}
          {row.reconstructed && (
            <span className="tl-rc" title={RECONSTRUCTED_TITLE} aria-label="reconstructed night">
              †
            </span>
          )}
        </div>
        <div role="cell" className="tl-c tl-c-fired" data-label="fired" title={row.event_date ?? "no fire date stated"}>
          {row.event_date ? fmtDate(row.event_date) : <span className="tl-quiet">undated</span>}
          {note && <span className="tl-note">{note}</span>}
        </div>
        <div role="cell" className="tl-c tl-c-tk">
          <button
            type="button"
            className="tl-tk"
            title={`Open ${row.thesis_name}${row.ticker ? ` on ${row.ticker}` : ""}`}
            onClick={() => onSelectThesis(row.thesis_id, row.ticker ?? row.security_id)}
          >
            {row.ticker ?? "—"}
          </button>
        </div>
        <div role="cell" className="tl-c tl-c-card" title={row.thesis_name}>
          <button
            type="button"
            className="tl-chip tl-chip-btn"
            aria-label={`Open ${row.thesis_name} in the Cockpit`}
            onClick={() => onSelectThesis(row.thesis_id)}
          >
            {row.thesis_name}
          </button>
        </div>
        <div role="cell" className="tl-c tl-c-kind">
          <span className="tl-role" role="img" aria-label={ROLE_LABEL[row.role]} title={ROLE_LABEL[row.role]}>
            {ROLE_GLYPH[row.role]}
          </span>{" "}
          {kindWords(row.kind)}
        </div>
        <div role="cell" className="tl-c tl-c-turns" data-label="turns">
          {turnsWords(row)}
        </div>
        <div role="cell" className="tl-c tl-c-grade" data-label="grade">
          {grade ?? <span className="tl-quiet">—</span>}
        </div>
        <div role="cell" className="tl-c tl-c-label" title={labelTitle(row)}>
          {row.label}
        </div>
        <div role="cell" className="tl-c tl-c-src" data-label="source">
          {row.sources.length === 0 ? (
            <span className="tl-quiet" title="no source stated">
              —
            </span>
          ) : (
            row.sources.map((s, i) => {
              const v = sourceView(s);
              return v.href ? (
                <ExternalLink
                  key={i}
                  className="tl-link"
                  href={v.href}
                  title={v.title}
                  ariaLabel={`Open ${row.ticker ?? "the name"} source ${s.ref ?? s.source}`}
                >
                  {v.text}
                </ExternalLink>
              ) : (
                <span key={i} className="tl-bar" title={v.title}>
                  {v.text}
                </span>
              );
            })
          )}
          {hasDetails && (
            <button
              type="button"
              className="tl-det-btn"
              aria-expanded={expanded}
              aria-label={`${expanded ? "Hide" : "Show"} details for ${row.ticker ?? row.thesis_name}`}
              onClick={onToggleExpand}
            >
              {expanded ? "▾" : "▸"}
            </button>
          )}
        </div>
        <StateCell row={row} />
        <div role="cell" className={`tl-c tl-c-live${row.live === true ? " on" : ""}`}>
          {liveWords(row)}
        </div>
        <div role="cell" className="tl-c tl-c-sess" data-label="sessions" title="sessions between the entry bar and the as-of, on IWM's calendar">
          {row.sessions_since ?? "—"}
        </div>
        <SinceCell row={row} />
      </div>
      {expanded && hasDetails && (
        <div className="tl-det" data-testid="tl-row-detail">
          {row.label_changes > 0 && (
            <div>Label changed {plural(row.label_changes, "time", "times")} after the first night</div>
          )}
          {sourceDetails.map((s, i) => (
            <div key={i}>
              <span className="tl-det-src">
                {s.source}
                {s.ref ? ` · ${s.ref}` : ""}:
              </span>{" "}
              {s.detail.join(" · ")}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

const WK_W = 960;
const WK_H = 42;
const WK_BASE = 28;
const WK_MAX = 24;

/** Rule-6-safe monochrome weekly trigger-frequency strip: pure counts (entry triggers bottom, risk signals top),
 *  click a week bar to filter to that week (click again to clear). Zero-shift fixed-height hover readout. */
function WeeklyStrip({
  rows,
  activeWeek,
  onPickWeek,
}: {
  rows: readonly Row[];
  activeWeek: string;
  onPickWeek: (week: string) => void;
}) {
  const [hoverWeek, setHoverWeek] = useState<string | null>(null);
  const weeks = useMemo(() => {
    const map = new Map<string, { week: string; entry: number; risk: number }>();
    for (const r of rows) {
      const cur = map.get(r.week) ?? { week: r.week, entry: 0, risk: 0 };
      if (r.role === "entry_trigger") cur.entry += 1;
      else cur.risk += 1;
      map.set(r.week, cur);
    }
    return [...map.values()].sort((a, b) => a.week.localeCompare(b.week));
  }, [rows]);

  if (weeks.length < 2) return null;

  const maxTotal = Math.max(1, ...weeks.map((w) => w.entry + w.risk));
  const totalEntry = weeks.reduce((s, w) => s + w.entry, 0);
  const totalRisk = weeks.reduce((s, w) => s + w.risk, 0);
  const padL = 6;
  const padR = 6;
  const step = (WK_W - padL - padR) / weeks.length;
  const barW = Math.max(4, Math.min(24, step * 0.65));
  const hovered = hoverWeek ? weeks.find((w) => w.week === hoverWeek) ?? null : null;
  const readout = hovered
    ? `${weekLabel(hovered.week)}: ${roleWords(hovered.entry, hovered.risk)}`
    : activeWeek
      ? `Filtered to ${weekLabel(activeWeek)} · click bar again to show all weeks`
      : "Hover a week to inspect, click to filter";

  return (
    <div className="tl-weekly" data-testid="tl-weekly">
      <div className="tl-wk-head">
        <span className="tl-wk-title">Weekly counts</span>
        <span className="tl-wk-meta">
          {roleWords(totalEntry, totalRisk)} · {plural(weeks.length, "week", "weeks")}
        </span>
      </div>
      <div className={`tl-wk-read${hovered || activeWeek ? " active" : ""}`} aria-live="polite">
        {readout}
      </div>
      <svg
        className="tl-wk-svg"
        viewBox={`0 0 ${WK_W} ${WK_H}`}
        preserveAspectRatio="xMinYMid meet"
        role="img"
        aria-label="Weekly trigger counts"
        onMouseLeave={() => setHoverWeek(null)}
      >
        <line className="tl-wk-axis" x1={padL} x2={WK_W - padR} y1={WK_BASE} y2={WK_BASE} />
        <text className="tl-wk-tick" x={padL} y={39} textAnchor="start">
          {fmtDate(weeks[0].week)}
        </text>
        <text className="tl-wk-tick" x={WK_W - padR} y={39} textAnchor="end">
          {fmtDate(weeks[weeks.length - 1].week)}
        </text>
        {weeks.map((w, i) => {
          const cx = padL + (i + 0.5) * step;
          const total = w.entry + w.risk;
          const totalH = Math.max(2, (Math.sqrt(total) / Math.sqrt(maxTotal)) * WK_MAX);
          const entryH = w.entry > 0 ? Math.max(2, (w.entry / total) * totalH) : 0;
          const riskH = Math.max(0, totalH - entryH);
          const isOn = activeWeek === w.week;
          const isDim = activeWeek !== "" && !isOn;
          return (
            <g
              key={w.week}
              className={`tl-wk-bar${hoverWeek === w.week || isOn ? " hi" : ""}${isDim ? " dim" : ""}`}
              onMouseEnter={() => setHoverWeek(w.week)}
              onClick={() => onPickWeek(isOn ? "" : w.week)}
            >
              <rect className="tl-wk-hit" x={cx - step / 2} y={2} width={step} height={WK_BASE} />
              {riskH > 0 && (
                <rect className="tl-wk-risk" x={cx - barW / 2} y={WK_BASE - entryH - riskH} width={barW} height={riskH} rx={1} />
              )}
              {entryH > 0 && (
                <rect className="tl-wk-entry" x={cx - barW / 2} y={WK_BASE - entryH} width={barW} height={entryH} rx={1} />
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** A chip row for one filter dimension: each chip restates how many rows it shows with every other filter
 *  applied. Renders nothing when the payload holds fewer than two values (it could not discriminate). */
function Chips({
  dim,
  label,
  rows,
  filters,
  selected,
  text,
  onToggle,
}: {
  dim: Dim;
  label: string;
  rows: readonly Row[];
  filters: Filters;
  selected: ReadonlySet<string>;
  text: (value: string) => string;
  onToggle: (value: string) => void;
}) {
  const values = dimValues(rows, dim);
  if (values.length < 2) return null;
  const counts = facetCounts(rows, filters, dim);
  return (
    <div className="tl-seg" role="group" aria-label={label}>
      <span className="tl-seg-label">{label}</span>
      {values.map((v) => (
        <button
          key={v}
          type="button"
          className={`tl-chipbtn${selected.has(v) ? " on" : ""}`}
          aria-pressed={selected.has(v)}
          onClick={() => onToggle(v)}
        >
          {text(v)} <span className="tl-n">{counts.get(v) ?? 0}</span>
        </button>
      ))}
    </div>
  );
}

function NamesNote({ unpriced, flagged }: { unpriced: TriggerLogNameOut[]; flagged: TriggerLogNameOut[] }) {
  const [open, setOpen] = useState(false);
  if (unpriced.length === 0 && flagged.length === 0) return null;
  const who = (n: TriggerLogNameOut) => n.ticker ?? n.security_id;
  return (
    <div className="dash-note tl-work">
      <button type="button" className="tl-fold" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="tl-chev" aria-hidden="true">
          {open ? "▾" : "▸"}
        </span>
        {[
          unpriced.length > 0 ? `${plural(unpriced.length, "name", "names")} unpriced (no bar since the recorded night)` : null,
          flagged.length > 0 ? `${plural(flagged.length, "name", "names")} left unpriced for a flagged price history` : null,
        ]
          .filter(Boolean)
          .join(" · ")}
      </button>
      {open && (
        <ul className="tl-names">
          {unpriced.map((n) => (
            <li key={`u-${n.security_id}`}>
              {who(n)}: unpriced
            </li>
          ))}
          {flagged.map((n) => (
            <li key={`f-${n.security_id}`}>
              {who(n)}: flagged, {n.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function toggled<T>(set: ReadonlySet<T>, value: T): Set<T> {
  const next = new Set(set);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

export default function TriggerLog({ asof, onSelectThesis }: DashboardProps) {
  const [days, setDays] = useState<TriggerLogDays>(DEFAULT_DAYS);
  const [role, setRole] = useState<TriggerLogRole>(DEFAULT_ROLE);
  const [groupBy, setGroupBy] = useState<GroupBy>("week");
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const [expandedRows, setExpandedRows] = useState<ReadonlySet<string>>(new Set());
  const q = useTriggerLog(asof, days, role);
  const data: TriggerLogOut | undefined = q.data;
  const rows = useMemo(() => data?.rows ?? [], [data]);

  const filtered = useMemo(() => filterRows(rows, filters), [rows, filters]);
  const groups = useMemo(
    () => groupRows(filtered, groupBy).map((g) => ({ ...g, rows: sortGroupRows(g.rows, sort) })),
    [filtered, groupBy, sort],
  );
  const filtering = filtersActive(filters);
  const stale = data != null && (data.asof !== asof || data.window_days !== days || data.role !== role);

  const cards = useMemo(
    () => dimValues(rows, "card").map((id) => ({ id, name: rows.find((r) => r.thesis_id === id)?.thesis_name ?? id })),
    [rows],
  );
  const cardCounts = useMemo(() => facetCounts(rows, filters, "card"), [rows, filters]);

  const toggles: { key: Toggle; label: string; title: string }[] = [
    { key: "liveOnly", label: "Live only", title: "Only triggers still on the card in effect at the as-of" },
    {
      key: "changedOnly",
      label: "State changed only",
      title: "Only triggers recorded on a night the card's state moved against the night before",
    },
  ];

  return (
    <div className="tl">
      <div className="tl-controls">
        <div className="tl-seg" role="group" aria-label="Window">
          <span className="tl-seg-label">Window</span>
          {DAYS_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`tl-chipbtn${days === o.value ? " on" : ""}`}
              aria-pressed={days === o.value}
              onClick={() => setDays(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <div className="tl-seg" role="group" aria-label="Load">
          <span className="tl-seg-label">Load</span>
          {ROLE_OPTIONS.map((o) => {
            const otherHint =
              data &&
              ((data.role === "entry" && o.value === "risk") || (data.role === "risk" && o.value === "entry")) &&
              data.counts.other_role_rows > 0
                ? ` (${data.counts.other_role_rows.toLocaleString("en-US")} in window)`
                : "";
            return (
              <button
                key={o.value}
                type="button"
                className={`tl-chipbtn${role === o.value ? " on" : ""}`}
                aria-pressed={role === o.value}
                title={`${o.title}${otherHint}`}
                onClick={() => setRole(o.value)}
              >
                {o.label}
              </button>
            );
          })}
        </div>
        <div className="tl-seg" role="group" aria-label="Group by">
          <span className="tl-seg-label">Group</span>
          {GROUP_BYS.map((g) => (
            <button
              key={g.key}
              type="button"
              className={`tl-chipbtn${groupBy === g.key ? " on" : ""}`}
              aria-pressed={groupBy === g.key}
              onClick={() => setGroupBy(g.key)}
            >
              {g.label}
            </button>
          ))}
        </div>
        {q.isFetching && data && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      {q.isLoading || (!data && q.isFetching) ? (
        <div className="dash-note">{SLOW}</div>
      ) : q.error || !data ? (
        <div className="dash-note">Could not load the Trigger Log.</div>
      ) : (
        <>
          <RecordStartNote empty={data.rows.length === 0} />
          <WeeklyStrip
            rows={rows}
            activeWeek={filters.week ?? ""}
            onPickWeek={(week) => setFilters((f) => ({ ...f, week }))}
          />
          <div className="tl-filters">
            {rows.length > 1 && (
              <label className="tl-field">
                <span className="tl-seg-label">Search</span>
                <input
                  type="search"
                  className="tl-search"
                  aria-label="Find ticker or label"
                  placeholder="Ticker or label"
                  value={filters.text ?? ""}
                  onChange={(e) => setFilters((f) => ({ ...f, text: e.target.value }))}
                />
              </label>
            )}
            <Chips
              dim="role"
              label="Role"
              rows={rows}
              filters={filters}
              selected={filters.roles}
              text={(v) => ROLE_LABEL[v as Role]}
              onToggle={(v) => setFilters((f) => ({ ...f, roles: toggled(f.roles, v as Role) }))}
            />
            <Chips
              dim="kind"
              label="Kind"
              rows={rows}
              filters={filters}
              selected={filters.kinds}
              text={kindWords}
              onToggle={(v) => setFilters((f) => ({ ...f, kinds: toggled(f.kinds, v) }))}
            />
            <Chips
              dim="grade"
              label="Grade"
              rows={rows}
              filters={filters}
              selected={filters.grades}
              text={(v) => GRADE_LABEL[v as GradeKey]}
              onToggle={(v) => setFilters((f) => ({ ...f, grades: toggled(f.grades, v as GradeKey) }))}
            />
            {cards.length > 1 && (
              <label className="tl-field">
                <span className="tl-seg-label">Card</span>
                <select
                  className="tl-select"
                  aria-label="Card"
                  value={filters.card}
                  onChange={(e) => setFilters((f) => ({ ...f, card: e.target.value }))}
                >
                  <option value="">All cards</option>
                  {cards.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} · {cardCounts.get(c.id) ?? 0}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {toggles.map(
              (t) =>
                toggleDiscriminates(rows, t.key) && (
                  <button
                    key={t.key}
                    type="button"
                    className={`tl-chipbtn${filters[t.key] ? " on" : ""}`}
                    aria-pressed={filters[t.key]}
                    title={t.title}
                    onClick={() => setFilters((f) => ({ ...f, [t.key]: !f[t.key] }))}
                  >
                    {t.label} <span className="tl-n">{toggleCount(rows, filters, t.key)}</span>
                  </button>
                ),
            )}
            {filtering && (
              <>
                <span className="tl-n tl-showing">
                  {filtered.length.toLocaleString("en-US")} of {rows.length.toLocaleString("en-US")}
                </span>
                <button type="button" className="tl-clear" onClick={() => setFilters(DEFAULT_FILTERS)}>
                  clear filters
                </button>
              </>
            )}
          </div>

          {data.counts.cards_counted === 0 ? (
            <div className="dash-note tl-empty">No card existed by this as-of.</div>
          ) : rows.length === 0 ? (
            <div className="dash-note tl-empty">{emptyWords(data)}</div>
          ) : filtered.length === 0 ? (
            <div className="dash-note tl-empty">No trigger passes the filters.</div>
          ) : (
            <div className="tl-scroll">
              <div className="tl-table" role="table" aria-label="Triggers">
                <div className="tl-head" role="row">
                  {COLUMNS.map((c) => {
                    const isSorted = c.sortKey != null && sort.key === c.sortKey;
                    return (
                      <div
                        key={c.key}
                        role="columnheader"
                        className={`tl-h tl-c-${c.key}`}
                        aria-sort={isSorted ? (sort.dir === "asc" ? "ascending" : "descending") : undefined}
                      >
                        {c.sortKey ? (
                          <button
                            type="button"
                            className={`tl-sort${isSorted ? " on" : ""}`}
                            onClick={() => setSort((s) => nextSort(s, c.sortKey!))}
                          >
                            {c.label}
                            {isSorted && <span className="tl-sort-arr">{sort.dir === "asc" ? " ↑" : " ↓"}</span>}
                          </button>
                        ) : (
                          c.label
                        )}
                      </div>
                    );
                  })}
                </div>
                {groups.map((g) => {
                  const isFolded = collapsed.has(g.key);
                  return (
                    <div key={g.key} role="rowgroup" aria-label={g.label}>
                      <div className="tl-gh" role="row">
                        <div role="columnheader" aria-colspan={COLUMNS.length} className="tl-gh-cell">
                          <button
                            type="button"
                            className="tl-gfold"
                            aria-expanded={!isFolded}
                            aria-label={`${isFolded ? "Expand" : "Collapse"} ${g.label}`}
                            onClick={() => setCollapsed((s) => toggled(s, g.key))}
                          >
                            {isFolded ? "▸" : "▾"}
                          </button>
                          {g.thesisId ? (
                            <button
                              type="button"
                              className="tl-cardname"
                              title={`Open ${g.label} in the Cockpit`}
                              onClick={() => onSelectThesis(g.thesisId as string)}
                            >
                              {g.label}
                            </button>
                          ) : (
                            <span className="tl-gname" title={groupBy === "week" ? g.key : undefined}>
                              {g.label}
                            </span>
                          )}
                          <span className="tl-n">{roleWords(g.entry, g.risk)}</span>
                        </div>
                      </div>
                      {!isFolded &&
                        g.rows.map((r) => (
                          <TriggerRow
                            key={r.key}
                            row={r}
                            expanded={expandedRows.has(r.key)}
                            onToggleExpand={() => setExpandedRows((s) => toggled(s, r.key))}
                            onSelectThesis={onSelectThesis}
                          />
                        ))}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {workLines(data, filtered.length, filtering).map((line, i) => (
            <div key={i} className="dash-note tl-work">
              {line}
            </div>
          ))}
          <NamesNote unpriced={data.unpriced} flagged={data.flagged} />
          <div className="dash-note tl-work">{cacheWords(data)}</div>
          <div className="dash-note tl-work tl-fixed">{NO_AGGREGATE}</div>
          <ul className="dash-note tl-limits">
            {LIMITS.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
