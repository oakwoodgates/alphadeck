import { useEffect, useMemo, useState } from "react";

import type { DashboardProps } from "../registry";
import "./rotation-quadrant.css";
import {
  DEFAULT_SORT,
  GROUP_BY_OPTIONS,
  NEUTRAL,
  QUADRANTS,
  changedQuadrant,
  clamp,
  fmtDay,
  fmtNum,
  focusedPlotDomain,
  groupLabel,
  moveWords,
  nextSort,
  plotDomain,
  quadrantLabel,
  sortGroups,
  statusWords,
  sx,
  sy,
  tailPoints,
  type Domain,
  type SortCol,
  type SortState,
} from "./quadrantView";
import {
  useRotationQuadrant,
  type GroupBy,
  type Quadrant,
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

type TailWindow = 3 | 5 | 9;
type ScaleMode = "all" | "focused";

const TAIL_OPTIONS: readonly { value: TailWindow; label: string }[] = [
  { value: 3, label: "2w" },
  { value: 5, label: "4w" },
  { value: 9, label: "8w" },
];

function QuadrantPlot({
  groups,
  groupBy,
  hover,
  selected,
  tailLen,
  scaleMode,
  onHover,
  onSelect,
}: {
  groups: readonly QuadrantGroupOut[];
  groupBy: GroupBy;
  hover: string | null;
  selected: string | null;
  tailLen: TailWindow;
  scaleMode: ScaleMode;
  onHover: (key: string | null) => void;
  onSelect: (key: string) => void;
}) {
  const pw = PLOT.w - PLOT.left - PLOT.right;
  const ph = PLOT.h - PLOT.top - PLOT.bottom;
  const dom: Domain =
    scaleMode === "focused" ? focusedPlotDomain(groups, tailLen) : plotDomain(groups);
  const Xraw = (v: number) => PLOT.left + sx(v, dom, pw);
  const Yraw = (v: number) => PLOT.top + sy(v, dom, ph);
  const X = (v: number) => clamp(Xraw(v), PLOT.left + 3, PLOT.left + pw - 3);
  const Y = (v: number) => clamp(Yraw(v), PLOT.top + 3, PLOT.top + ph - 3);
  const isClipped = (xv: number, yv: number) =>
    Math.abs(xv - NEUTRAL) > dom.hx || Math.abs(yv - NEUTRAL) > dom.hy;

  const cx = Xraw(NEUTRAL);
  const cy = Yraw(NEUTRAL);
  const qCounts: Record<Quadrant, number> = { improving: 0, leading: 0, weakening: 0, lagging: 0 };
  for (const g of groups) {
    if (g.quadrant) qCounts[g.quadrant]++;
  }
  const corner = [
    { key: "improving" as const, x: PLOT.left + 8, y: PLOT.top + 15, anchor: "start" as const, countDx: 66 },
    { key: "leading" as const, x: PLOT.left + pw - 8, y: PLOT.top + 15, anchor: "end" as const, countDx: -54 },
    { key: "lagging" as const, x: PLOT.left + 8, y: PLOT.top + ph - 8, anchor: "start" as const, countDx: 54 },
    { key: "weakening" as const, x: PLOT.left + pw - 8, y: PLOT.top + ph - 8, anchor: "end" as const, countDx: -72 },
  ];

  const activeKey = hover ?? selected;
  const activeGroup = activeKey ? groups.find((g) => g.key === activeKey) ?? null : null;
  const hasFocus = activeGroup != null;
  // the hovered and selected dots draw last, so they sit on top
  const ordered = [...groups].sort(
    (a, b) => Number(a.key === hover || a.key === selected) - Number(b.key === hover || b.key === selected),
  );
  // subtle midpoint ticks between center and edges
  const xTicks = [NEUTRAL - dom.hx, NEUTRAL - dom.hx / 2, NEUTRAL, NEUTRAL + dom.hx / 2, NEUTRAL + dom.hx];
  const yTicks = [NEUTRAL - dom.hy, NEUTRAL - dom.hy / 2, NEUTRAL, NEUTRAL + dom.hy / 2, NEUTRAL + dom.hy];

  return (
    <svg
      className="rq-plot"
      viewBox={`0 0 ${PLOT.w} ${PLOT.h}`}
      role="img"
      aria-label={`Rotation quadrant: ${plural(groups.length, "group", "groups")} plotted`}
    >
      {/* subtle quadrant washes for spatial orientation */}
      <rect className="rq-wash improving" x={PLOT.left} y={PLOT.top} width={cx - PLOT.left} height={cy - PLOT.top} />
      <rect className="rq-wash leading" x={cx} y={PLOT.top} width={PLOT.left + pw - cx} height={cy - PLOT.top} />
      <rect className="rq-wash lagging" x={PLOT.left} y={cy} width={cx - PLOT.left} height={PLOT.top + ph - cy} />
      <rect className="rq-wash weakening" x={cx} y={cy} width={PLOT.left + pw - cx} height={PLOT.top + ph - cy} />
      <rect className="rq-frame" x={PLOT.left} y={PLOT.top} width={pw} height={ph} />

      {/* faint intermediate gridlines */}
      {[xTicks[1], xTicks[3]].map((v) => (
        <line key={`gx${v}`} className="rq-grid" x1={Xraw(v)} x2={Xraw(v)} y1={PLOT.top} y2={PLOT.top + ph} />
      ))}
      {[yTicks[1], yTicks[3]].map((v) => (
        <line key={`gy${v}`} className="rq-grid" x1={PLOT.left} x2={PLOT.left + pw} y1={Yraw(v)} y2={Yraw(v)} />
      ))}

      {corner.map((c) => (
        <g key={c.key}>
          <text className={`rq-qlabel ${c.key}`} x={c.x} y={c.y} textAnchor={c.anchor}>
            {quadrantLabel(c.key)}
          </text>
          <text className="rq-qcount" x={c.x + c.countDx} y={c.y} textAnchor={c.anchor}>
            {qCounts[c.key]}
          </text>
        </g>
      ))}
      <line className="rq-axis" x1={cx} x2={cx} y1={PLOT.top} y2={PLOT.top + ph} />
      <line className="rq-axis" x1={PLOT.left} x2={PLOT.left + pw} y1={cy} y2={cy} />
      {xTicks.map((v, i) => (
        <text
          key={`x${i}`}
          className="rq-tick"
          x={Xraw(v)}
          y={PLOT.top + ph + 13}
          textAnchor={i === 0 ? "start" : i === xTicks.length - 1 ? "end" : "middle"}
        >
          {v.toFixed(1)}
        </text>
      ))}
      {yTicks.map((v, i) => (
        <text key={`y${i}`} className="rq-tick" x={PLOT.left - 5} y={Yraw(v) + 3} textAnchor="end">
          {v.toFixed(1)}
        </text>
      ))}
      <text className="rq-tick rq-axis-title" x={PLOT.left + pw} y={PLOT.h - 3} textAnchor="end">
        x · RS level →
      </text>
      <text className="rq-tick rq-axis-title" x={PLOT.left} y={PLOT.top - 8} textAnchor="start">
        ↑ y · RS momentum
      </text>

      {/* crosshair drop-lines for the hovered/selected dot */}
      {activeGroup && activeGroup.x != null && activeGroup.y != null && (
        <g className="rq-crosshair" aria-hidden="true">
          <line x1={X(activeGroup.x)} x2={X(activeGroup.x)} y1={PLOT.top} y2={PLOT.top + ph} />
          <line x1={PLOT.left} x2={PLOT.left + pw} y1={Y(activeGroup.y)} y2={Y(activeGroup.y)} />
        </g>
      )}

      <g className="rq-tails">
        {ordered.map((g) => {
          const pts = tailPoints(g, tailLen);
          if (pts.length < 2) return null;
          const on = g.key === hover || g.key === selected;
          const dim = hasFocus && !on;
          return (
            <g key={g.key}>
              <polyline
                data-testid="rq-tail"
                data-key={g.key}
                className={`rq-tail ${g.quadrant ?? ""}${on ? " on" : ""}${dim ? " dim" : ""}`}
                points={pts.map((p) => `${X(p.x).toFixed(1)},${Y(p.y).toFixed(1)}`).join(" ")}
              />
              {on &&
                pts.slice(0, -1).map((p, idx) => (
                  <circle
                    key={`tn-${idx}`}
                    className={`rq-tail-node ${g.quadrant ?? ""}`}
                    cx={X(p.x)}
                    cy={Y(p.y)}
                    r={1.6 + (idx / Math.max(1, pts.length - 1)) * 1.1}
                    opacity={0.35 + (idx / Math.max(1, pts.length - 1)) * 0.55}
                  >
                    <title>
                      {p.session ? `${fmtDay(p.session)} · ` : ""}x {fmtNum(p.x)} · y {fmtNum(p.y)}
                    </title>
                  </circle>
                ))}
            </g>
          );
        })}
      </g>
      <g className="rq-dots">
        {ordered.map((g) => {
          if (g.x == null || g.y == null) return null;
          const on = g.key === hover || g.key === selected;
          const dim = hasFocus && !on;
          const label = groupLabel(g, groupBy);
          const px = X(g.x);
          const py = Y(g.y);
          const clipped = isClipped(g.x, g.y);
          // clamp label box safely inside the plot frame
          const onRight = px > PLOT.left + pw / 2;
          const labelW = Math.min(220, label.length * 6.2 + 12);
          const lx = onRight
            ? clamp(px - 8, PLOT.left + labelW + 4, PLOT.left + pw - 4)
            : clamp(px + 8, PLOT.left + 4, PLOT.left + pw - labelW - 4);
          const ly = clamp(py - 9, PLOT.top + 16, PLOT.top + ph - 6);
          return (
            <g
              key={g.key}
              data-testid="rq-dot"
              data-key={g.key}
              className={`rq-dot-g${g.key === selected ? " selected" : ""}${g.key === hover ? " hover" : ""}${dim ? " dim" : ""}`}
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
              <circle className="rq-dot-hit" cx={px} cy={py} r={10} />
              {clipped && <circle className={`rq-dot-clip ${g.quadrant ?? ""}`} cx={px} cy={py} r={on ? 7.5 : 6} />}
              <circle className={`rq-dot ${g.quadrant ?? ""}`} cx={px} cy={py} r={on ? 5.2 : 3.6} />
              {on && (
                <g className="rq-dot-pill">
                  <rect
                    className="rq-dot-bg"
                    x={onRight ? lx - labelW : lx - 4}
                    y={ly - 11}
                    width={labelW + 4}
                    height={15}
                    rx={3}
                  />
                  <text
                    className="rq-dot-label"
                    x={lx}
                    y={ly}
                    textAnchor={onRight ? "end" : "start"}
                  >
                    {label}
                  </text>
                </g>
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
  hovered,
  minPriced,
  pricedSessions,
  onHover,
  onToggle,
  onSelect,
}: {
  g: QuadrantGroupOut;
  groupBy: GroupBy;
  open: boolean;
  selected: boolean;
  hovered: boolean;
  minPriced: number;
  pricedSessions: number;
  onHover: (key: string | null) => void;
  onToggle: () => void;
  onSelect: DashboardProps["onSelectThesis"];
}) {
  const status = statusWords(g, minPriced);
  const silent = groupBy === "link" && (g.link === "Context" || g.link === "Bench");
  const fullLabel = groupLabel(g, groupBy);
  return (
    <>
      <tr
        id={`rq-row-${g.key}`}
        className={`rq-group${open ? " open" : ""}${selected ? " selected" : ""}${hovered ? " hovered" : ""}${silent || status ? " quiet" : ""}`}
        tabIndex={0}
        aria-expanded={open}
        aria-selected={selected}
        onMouseEnter={() => g.status === "plotted" && onHover(g.key)}
        onMouseLeave={() => g.status === "plotted" && onHover(null)}
        onClick={onToggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onToggle();
          }
        }}
      >
        <td className="rq-label" title={fullLabel}>
          <span className="rq-chev" aria-hidden="true">
            {open ? "▾" : "▸"}
          </span>
          <span className="rq-label-text">{fullLabel}</span>
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
  const [quadFilter, setQuadFilter] = useState<Quadrant | null>(null);
  const [query, setQuery] = useState("");
  const [tailLen, setTailLen] = useState<TailWindow>(9);
  const [scaleMode, setScaleMode] = useState<ScaleMode>("all");
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [selected, setSelected] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);
  const [scrollTo, setScrollTo] = useState<string | null>(null);
  const q = useRotationQuadrant(asof, groupBy, includeSilent);
  const data = q.data;

  const shown = useMemo(() => {
    if (!data) return [];
    const needle = query.trim().toLowerCase();
    return data.groups.filter((g) => {
      if (changedOnly && !changedQuadrant(g)) return false;
      if (quadFilter != null && g.quadrant !== quadFilter) return false;
      if (needle && !groupLabel(g, data.group_by).toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [data, changedOnly, quadFilter, query]);
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
        <div className="rq-seg" role="group" aria-label="Tail length">
          <span className="rq-seg-label">Tail</span>
          {TAIL_OPTIONS.map((o) => (
            <button
              key={o.value}
              type="button"
              className={`rq-seg-btn${tailLen === o.value ? " on" : ""}`}
              aria-pressed={tailLen === o.value}
              onClick={() => setTailLen(o.value)}
            >
              {o.label}
            </button>
          ))}
        </div>
        <div className="rq-seg" role="group" aria-label="Plot scale">
          <span className="rq-seg-label">Scale</span>
          {(
            [
              { value: "all", label: "Fit all", title: "Fit every dot and tail point inside the plot" },
              {
                value: "focused",
                label: "Focused",
                title: "Zoom to the 88th percentile around 100 so extreme outliers do not flatten the center",
              },
            ] as const
          ).map((o) => (
            <button
              key={o.value}
              type="button"
              className={`rq-seg-btn${scaleMode === o.value ? " on" : ""}`}
              aria-pressed={scaleMode === o.value}
              title={o.title}
              onClick={() => setScaleMode(o.value)}
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

      <div className="rq-controls rq-subcontrols">
        <div className="rq-seg" role="group" aria-label="Filter quadrant">
          <span className="rq-seg-label">Quadrant</span>
          <button
            type="button"
            className={`rq-seg-btn${quadFilter == null ? " on" : ""}`}
            aria-pressed={quadFilter == null}
            onClick={() => setQuadFilter(null)}
          >
            All
          </button>
          {QUADRANTS.map((qd) => (
            <button
              key={qd.key}
              type="button"
              className={`rq-seg-btn${quadFilter === qd.key ? " on" : ""}`}
              aria-pressed={quadFilter === qd.key}
              onClick={() => setQuadFilter((cur) => (cur === qd.key ? null : qd.key))}
            >
              <Swatch q={qd.key} /> {qd.label}
            </button>
          ))}
        </div>
        <input
          type="search"
          className="rq-search"
          aria-label="Filter groups"
          placeholder="Filter groups…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
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
              tailLen={tailLen}
              scaleMode={scaleMode}
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
                    hovered={hover === g.key}
                    minPriced={data.min_priced}
                    pricedSessions={data.priced_sessions}
                    onHover={setHover}
                    onToggle={() => toggle(g.key)}
                    onSelect={onSelectThesis}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      <div className="rq-foot">
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
      </div>
    </div>
  );
}
