import { useEffect, useMemo, useState } from "react";

import type { DashboardProps } from "../registry";
import "./rotation-quadrant.css";
import {
  DEFAULT_SORT,
  GROUP_BY_OPTIONS,
  NEUTRAL,
  QUADRANTS,
  changedQuadrant,
  fmtDay,
  fmtNum,
  groupLabel,
  moveWords,
  nextSort,
  plotDomain,
  quadrantLabel,
  sortGroups,
  statusWords,
  sx,
  sy,
  tailPath,
  type Domain,
  type SortCol,
  type SortState,
} from "./quadrantView";
import {
  useRotationQuadrant,
  type GroupBy,
  type QuadrantGroupOut,
  type QuadrantNameOut,
  type RotationQuadrantOut,
} from "./useRotationQuadrant";

// Rotation Quadrant (docs/dashboards/rotation-quadrant.md): each group's strength vs SPY (x) and that strength's
// momentum (y), with an 8-week tail, by link, business type, sector or card, at the as-of. Display only. Every
// number (x, y, the quadrants, the tails, who counts) is computed server-side; this page picks the view, draws
// the plot (inline SVG, no chart library), orders the table and words the numbers.

// Rosters are point-in-time only from this date (basket_snapshot); before it today's roster stands in.
const ROSTER_PIT_FROM = "2026-09-15";

const plural = (n: number, one: string, many: string) =>
  `${n.toLocaleString("en-US")} ${n === 1 ? one : many}`;

function cacheWords(d: RotationQuadrantOut): string {
  if (d.cache === "hit") return "cached";
  const s = d.compute_ms / 1000;
  return `computed in ${s < 10 ? s.toFixed(1) : Math.round(s)} s — first load since the last run or basket edit`;
}

/** The measures and their windows, every number off the payload. */
function measureLine(d: RotationQuadrantOut): string {
  return [
    "RS ratio = the group's equal-weighted line ÷ SPY's line",
    `x = RS level: 100 × the RS ratio ÷ its own ${d.level_sessions}-session average (100 = at it)`,
    `y = RS momentum: 100 × x ÷ x ${d.momentum_sessions} sessions ago (100 = flat)`,
    `tail: ${d.tail_points} points, every ${d.tail_step} sessions`,
    `4 weeks ago = ${d.ago_sessions} sessions back`,
  ].join(" · ");
}

/** The quiet line that shows the work. */
function workLine(d: RotationQuadrantOut, shown: number): string {
  const cards =
    d.cards_not_yet_created > 0
      ? `${plural(d.cards_counted, "live card", "live cards")} counted (${d.cards_not_yet_created} created after the as-of left out)`
      : `${plural(d.cards_counted, "live card", "live cards")} counted`;
  const groups = d.groups_plotted + d.groups_too_small + d.groups_no_history;
  return [
    cards,
    `${plural(groups, "group", "groups")}: ${d.groups_plotted} plotted · ${d.groups_too_small} too small (fewer than ${d.min_priced} priced names) · ${d.groups_no_history} not enough history`,
    shown !== groups ? `${shown} shown by the filter` : null,
    `${plural(d.companies, "company", "companies")}, ${d.priced_companies.toLocaleString("en-US")} priced (a daily return in the last ${d.priced_sessions} sessions)`,
    d.flagged_companies > 0
      ? `names with a flagged price history are left out (${plural(d.flagged_companies, "company", "companies")})`
      : "names with a flagged price history are left out",
    d.include_silent
      ? "Context and Bench included"
      : `Context and Bench left out (${plural(d.silent_rows_left_out, "basket row", "basket rows")})`,
    `${plural(d.unresolved_rows, "basket row", "basket rows")} without a resolved security not counted`,
    d.first_session
      ? `${plural(d.sessions, "session", "sessions")} read, ${fmtDay(d.first_session)} → ${fmtDay(d.last_session)}`
      : "no sessions read",
    d.spy_available ? "against SPY" : "no SPY tape at this as-of: nothing can be measured",
    d.session_source === "companies" ? "sessions from the companies' bars" : null,
    cacheWords(d),
    `as of ${fmtDay(d.asof)}`,
  ]
    .filter(Boolean)
    .join(" · ");
}

// --- the plot --------------------------------------------------------------------------------------------------

const PLOT = { w: 640, h: 450, left: 44, right: 12, top: 22, bottom: 30 };

function Swatch({ q }: { q: QuadrantGroupOut["quadrant"] }) {
  return <span className={`rq-sw${q ? ` ${q}` : ""}`} aria-hidden="true" />;
}

function Readout({ g, groupBy }: { g: QuadrantGroupOut | null; groupBy: GroupBy }) {
  if (!g) {
    return (
      <div className="rq-readout" data-testid="rq-readout">
        Hover a dot for its numbers; click one to find its row.
      </div>
    );
  }
  return (
    <div className="rq-readout" data-testid="rq-readout">
      <span className="rq-readout-name">{groupLabel(g, groupBy)}</span>
      <span>x {fmtNum(g.x)}</span>
      <span>y {fmtNum(g.y)}</span>
      <span>
        n {g.priced} priced of {g.names}
      </span>
      <span>
        <Swatch q={g.quadrant} /> {moveWords(g)}
      </span>
    </div>
  );
}

function QuadrantPlot({
  groups,
  groupBy,
  hover,
  selected,
  onHover,
  onSelect,
}: {
  groups: readonly QuadrantGroupOut[];
  groupBy: GroupBy;
  hover: string | null;
  selected: string | null;
  onHover: (key: string | null) => void;
  onSelect: (key: string) => void;
}) {
  const pw = PLOT.w - PLOT.left - PLOT.right;
  const ph = PLOT.h - PLOT.top - PLOT.bottom;
  const dom: Domain = plotDomain(groups);
  const X = (v: number) => PLOT.left + sx(v, dom, pw);
  const Y = (v: number) => PLOT.top + sy(v, dom, ph);
  const cx = X(NEUTRAL);
  const cy = Y(NEUTRAL);
  const corner = [
    { key: "improving", x: PLOT.left + 6, y: PLOT.top + 14, anchor: "start" },
    { key: "leading", x: PLOT.left + pw - 6, y: PLOT.top + 14, anchor: "end" },
    { key: "lagging", x: PLOT.left + 6, y: PLOT.top + ph - 8, anchor: "start" },
    { key: "weakening", x: PLOT.left + pw - 6, y: PLOT.top + ph - 8, anchor: "end" },
  ] as const;
  // the hovered and selected dots draw last, so they sit on top
  const ordered = [...groups].sort(
    (a, b) => Number(a.key === hover || a.key === selected) - Number(b.key === hover || b.key === selected),
  );
  return (
    <svg
      className="rq-plot"
      viewBox={`0 0 ${PLOT.w} ${PLOT.h}`}
      role="img"
      aria-label={`Rotation quadrant: ${plural(groups.length, "group", "groups")} plotted`}
    >
      <rect className="rq-frame" x={PLOT.left} y={PLOT.top} width={pw} height={ph} />
      {corner.map((c) => (
        <text key={c.key} className={`rq-qlabel ${c.key}`} x={c.x} y={c.y} textAnchor={c.anchor}>
          {quadrantLabel(c.key)}
        </text>
      ))}
      <line className="rq-axis" x1={cx} x2={cx} y1={PLOT.top} y2={PLOT.top + ph} />
      <line className="rq-axis" x1={PLOT.left} x2={PLOT.left + pw} y1={cy} y2={cy} />
      {([NEUTRAL - dom.hx, NEUTRAL, NEUTRAL + dom.hx] as const).map((v, i) => (
        <text
          key={`x${v}`}
          className="rq-tick"
          x={X(v)}
          y={PLOT.top + ph + 12}
          textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"}
        >
          {v.toFixed(1)}
        </text>
      ))}
      {[NEUTRAL - dom.hy, NEUTRAL, NEUTRAL + dom.hy].map((v) => (
        <text key={`y${v}`} className="rq-tick" x={PLOT.left - 5} y={Y(v) + 3} textAnchor="end">
          {v.toFixed(1)}
        </text>
      ))}
      <text className="rq-tick rq-axis-title" x={PLOT.left + pw} y={PLOT.h - 3} textAnchor="end">
        x · RS level →
      </text>
      <text className="rq-tick rq-axis-title" x={PLOT.left} y={PLOT.top - 8} textAnchor="start">
        ↑ y · RS momentum
      </text>
      <g className="rq-tails">
        {ordered.map((g) => {
          const pts = tailPath(g);
          if (pts.length < 2) return null;
          const on = g.key === hover || g.key === selected;
          return (
            <polyline
              key={g.key}
              data-testid="rq-tail"
              data-key={g.key}
              className={`rq-tail ${g.quadrant ?? ""}${on ? " on" : ""}`}
              points={pts.map((p) => `${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ")}
            />
          );
        })}
      </g>
      <g className="rq-dots">
        {ordered.map((g) => {
          if (g.x == null || g.y == null) return null;
          const on = g.key === hover || g.key === selected;
          const label = groupLabel(g, groupBy);
          const px = X(g.x);
          const py = Y(g.y);
          return (
            <g
              key={g.key}
              data-testid="rq-dot"
              data-key={g.key}
              className={`rq-dot-g${g.key === selected ? " selected" : ""}${g.key === hover ? " hover" : ""}`}
              role="button"
              tabIndex={0}
              aria-label={`${label}: ${quadrantLabel(g.quadrant)}, x ${fmtNum(g.x)}, y ${fmtNum(g.y)}`}
              aria-pressed={g.key === selected}
              onMouseEnter={() => onHover(g.key)}
              onMouseLeave={() => onHover(null)}
              onFocus={() => onHover(g.key)}
              onBlur={() => onHover(null)}
              onClick={() => onSelect(g.key)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  onSelect(g.key);
                }
              }}
            >
              <circle className={`rq-dot ${g.quadrant ?? ""}`} cx={px} cy={py} r={on ? 5 : 3.5} />
              {on && (
                <text
                  className="rq-dot-label"
                  x={px > PLOT.left + pw / 2 ? px - 8 : px + 8}
                  y={py - 7}
                  textAnchor={px > PLOT.left + pw / 2 ? "end" : "start"}
                >
                  {label}
                </text>
              )}
            </g>
          );
        })}
      </g>
    </svg>
  );
}

// --- the table ---------------------------------------------------------------------------------------------------

function SortTh({
  col,
  label,
  title,
  sort,
  onSort,
  num = false,
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
      className={num ? "rq-num" : undefined}
      aria-sort={dir === "asc" ? "ascending" : dir === "desc" ? "descending" : "none"}
    >
      <button type="button" className={`rq-sort${dir ? " active" : ""}`} title={title} onClick={() => onSort(col)}>
        {label}
        {dir && (
          <span className="rq-arrow" aria-hidden="true">
            {dir === "asc" ? "▲" : "▼"}
          </span>
        )}
      </button>
    </th>
  );
}

function nameNote(m: QuadrantNameOut, pricedSessions: number): string | null {
  if (m.flag != null) return `left out: ${m.flag}`;
  if (!m.priced) return `not priced: no daily return in the last ${pricedSessions} sessions`;
  return null;
}

function NameRow({
  name,
  groupBy,
  pricedSessions,
  onSelect,
}: {
  name: QuadrantNameOut;
  groupBy: GroupBy;
  pricedSessions: number;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const note = nameNote(name, pricedSessions);
  return (
    <tr className={`rq-name${note ? " quiet" : ""}`}>
      <td className="rq-name-id" colSpan={6}>
        <span className="rq-tk">{name.ticker}</span>
        <span className="rq-chips">
          {name.cards.map((c) => {
            const links = c.links.map((l) => l ?? "Unsegmented").join(", ");
            return (
              <button
                key={c.thesis_id}
                type="button"
                className="rq-chip"
                title={`Open ${c.thesis_name} on ${c.ticker}`}
                onClick={() => onSelect(c.thesis_id, c.ticker)}
              >
                {c.thesis_name}
                {groupBy !== "link" && links && <span className="rq-chip-links"> · {links}</span>}
              </button>
            );
          })}
        </span>
        {note && <span className="rq-off">{note}</span>}
      </td>
    </tr>
  );
}

function GroupRow({
  g,
  groupBy,
  open,
  selected,
  minPriced,
  pricedSessions,
  onToggle,
  onSelect,
}: {
  g: QuadrantGroupOut;
  groupBy: GroupBy;
  open: boolean;
  selected: boolean;
  minPriced: number;
  pricedSessions: number;
  onToggle: () => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const status = statusWords(g, minPriced);
  const silent = groupBy === "link" && (g.link === "Context" || g.link === "Bench");
  return (
    <>
      <tr
        id={`rq-row-${g.key}`}
        className={`rq-group${open ? " open" : ""}${selected ? " selected" : ""}${silent || status ? " quiet" : ""}`}
        tabIndex={0}
        aria-expanded={open}
        aria-selected={selected}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <td className="rq-label">
          <span className="rq-chev" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          <span className="rq-label-text">{groupLabel(g, groupBy)}</span>
          {status && <span className="rq-off">{status}</span>}
        </td>
        <td className="rq-q">
          <Swatch q={g.quadrant} /> {quadrantLabel(g.quadrant)}
        </td>
        <td className="rq-q">
          <Swatch q={g.quadrant_ago} /> {quadrantLabel(g.quadrant_ago)}
        </td>
        <td className="rq-num rq-v">{fmtNum(g.x)}</td>
        <td className="rq-num rq-v">{fmtNum(g.y)}</td>
        <td className="rq-num rq-v" title="names priced / names in the group">
          {g.priced}/{g.names}
        </td>
      </tr>
      {open && g.flagged.length > 0 && (
        <tr className="rq-name">
          <td colSpan={6} className="rq-name-id">
            <span
              className="rq-note"
              title="their data flags reach their price readings (the Cockpit grays those cells), so they never contribute a daily return"
            >
              {plural(g.flagged.length, "name", "names")} left out:{" "}
              {g.flagged.map((f) => `${f.ticker}, ${f.reason}`).join(" · ")}
            </span>
          </td>
        </tr>
      )}
      {open && g.sessions_flat > 0 && (
        <tr className="rq-name">
          <td colSpan={6} className="rq-name-id">
            <span className="rq-note">
              {plural(g.sessions_flat, "session", "sessions")} carried flat in the span the measures read (no
              name had a bar)
            </span>
          </td>
        </tr>
      )}
      {open &&
        g.members.map((m) => (
          <NameRow
            key={m.security_id}
            name={m}
            groupBy={groupBy}
            pricedSessions={pricedSessions}
            onSelect={onSelect}
          />
        ))}
    </>
  );
}

// --- the page ----------------------------------------------------------------------------------------------------

export default function RotationQuadrant({ asof, onSelectThesis }: DashboardProps) {
  const [groupBy, setGroupBy] = useState<GroupBy>("link");
  const [includeSilent, setIncludeSilent] = useState(false);
  const [sort, setSort] = useState<SortState>(DEFAULT_SORT);
  const [changedOnly, setChangedOnly] = useState(false);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [scrollTo, setScrollTo] = useState<string | null>(null);
  const q = useRotationQuadrant(asof, groupBy, includeSilent);
  const data = q.data;

  const shown = useMemo(
    () => (data ? data.groups.filter((g) => !changedOnly || changedQuadrant(g)) : []),
    [data, changedOnly],
  );
  const rows = useMemo(() => (data ? sortGroups(shown, sort, data.group_by) : []), [data, shown, sort]);
  const plotted = useMemo(() => shown.filter((g) => g.status === "plotted"), [shown]);

  // a dot's click brings its row into view
  useEffect(() => {
    if (!scrollTo) return;
    const el = document.getElementById(`rq-row-${scrollTo}`);
    if (el && typeof el.scrollIntoView === "function") el.scrollIntoView({ block: "nearest" });
    setScrollTo(null);
  }, [scrollTo]);

  if (q.isLoading) {
    return (
      <div className="dash-note">
        Computing every group's line — the first load after a run or a basket edit can take a minute.
      </div>
    );
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Rotation Quadrant.</div>;

  const view = data.group_by;
  const focus = data.groups.find((g) => g.key === (hover ?? selected)) ?? null;
  const toggle = (key: string) => {
    setSelected(key);
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };
  const selectDot = (key: string) => {
    setSelected(key);
    setScrollTo(key);
  };
  const onSort = (col: SortCol) => setSort((s) => nextSort(s, col));
  const resetView = () => {
    setOpen(new Set());
    setSelected(null);
    setHover(null);
  };

  return (
    <div className="rq">
      <div className="rq-controls">
        <div className="rq-seg" role="group" aria-label="Group by">
          <span className="rq-seg-label">Group by</span>
          {GROUP_BY_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`rq-seg-btn${groupBy === o.value ? " on" : ""}`}
              aria-pressed={groupBy === o.value}
              onClick={() => {
                setGroupBy(o.value);
                resetView();
              }}
            >
              {o.label}
            </button>
          ))}
        </div>
        <label className="rq-check" title="Add Context and Bench memberships (their own groups in the Link view)">
          <input
            type="checkbox"
            checked={includeSilent}
            onChange={(e) => {
              setIncludeSilent(e.target.checked);
              resetView();
            }}
          />
          Show Context and Bench
        </label>
        <label className="rq-check" title="Only groups whose quadrant now differs from 4 weeks ago (both known)">
          <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} />
          Changed quadrant in the last 4 weeks
        </label>
        {q.isFetching && (
          <span className="dash-note">
            {data.asof !== asof || data.include_silent !== includeSilent
              ? "Computing every group's line — the first load after a run or a basket edit can take a minute."
              : "updating…"}
          </span>
        )}
      </div>
      <div className="dash-note rq-work">{measureLine(data)}</div>
      <div className="dash-note rq-work">{workLine(data, shown.length)}</div>
      {data.roster_fallback_cards > 0 && (
        <div className="dash-note rq-work">
          Today's roster stands in for {plural(data.roster_fallback_cards, "card", "cards")}: rosters are
          point-in-time only from {fmtDay(ROSTER_PIT_FROM)}.
        </div>
      )}
      <div className="dash-note rq-legend">
        Our own simple measures, not a published methodology. Equal weight. Business type and sector come from
        today's master. Lagging by construction: x compares against a {data.level_sessions}-session average.{" "}
        {QUADRANTS.map((x) => `${x.label}: ${x.rule}`).join(" · ")}. Each group's basket is as of{" "}
        {fmtDay(data.asof)}, looking back.
      </div>

      <div className="rq-body">
        <div className="rq-plot-wrap">
          <Readout g={focus} groupBy={view} />
          {plotted.length === 0 ? (
            <div className="dash-note">No group in this view can be plotted.</div>
          ) : (
            <QuadrantPlot
              groups={plotted}
              groupBy={view}
              hover={hover}
              selected={selected}
              onHover={setHover}
              onSelect={selectDot}
            />
          )}
        </div>

        {rows.length === 0 ? (
          <div className="dash-note">
            {changedOnly ? "No group changed quadrant in the last 4 weeks." : "No companies in this view."}
          </div>
        ) : (
          <div className="rq-scroll">
            <table className="rq-table">
              <thead>
                <tr>
                  <SortTh
                    col="label"
                    label={GROUP_BY_OPTIONS.find((o) => o.value === view)?.label ?? "Group"}
                    title="Sort by name"
                    sort={sort}
                    onSort={onSort}
                  />
                  <SortTh
                    col="quadrant"
                    label="Now"
                    title="Sort by quadrant now: Improving, Leading, Weakening, Lagging, then x"
                    sort={sort}
                    onSort={onSort}
                  />
                  <SortTh
                    col="quadrant_ago"
                    label="4 weeks ago"
                    title={`Sort by the quadrant ${data.ago_sessions} sessions back`}
                    sort={sort}
                    onSort={onSort}
                  />
                  <SortTh col="x" label="x" title="Sort by RS level" sort={sort} onSort={onSort} num />
                  <SortTh col="y" label="y" title="Sort by RS momentum" sort={sort} onSort={onSort} num />
                  <SortTh
                    col="names"
                    label="Priced"
                    title="Sort by names priced (of the names in the group)"
                    sort={sort}
                    onSort={onSort}
                    num
                  />
                </tr>
              </thead>
              <tbody>
                {rows.map((g) => (
                  <GroupRow
                    key={g.key}
                    g={g}
                    groupBy={view}
                    open={open.has(g.key)}
                    selected={selected === g.key}
                    minPriced={data.min_priced}
                    pricedSessions={data.priced_sessions}
                    onToggle={() => toggle(g.key)}
                    onSelect={onSelectThesis}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
