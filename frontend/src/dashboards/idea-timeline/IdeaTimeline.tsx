import { useMemo, useState } from "react";

import { fmtDate, verdictLabel } from "../../util/format";
import type { DashboardProps } from "../registry";
import "./idea-timeline.css";
import {
  catalystTitle,
  dailyStateCounts,
  DECISION_GLYPH,
  decisionTitle,
  dayNum,
  entryTitle,
  exitTitle,
  filterRows,
  fmtDay,
  inView,
  markersLater,
  monthTicks,
  pct,
  plural,
  segmentTitle,
  sortRows,
  SORTS,
  spanStyle,
  stateCls,
  stateKey,
  stateLabel,
  STATES,
  viewFor,
  workLine,
  ZOOMS,
  type DayStateCounts,
  type SortKey,
  type Tick,
  type View,
  type Zoom,
} from "./timeline";
import { useIdeaTimeline, type IdeaTimelineRowOut } from "./useIdeaTimeline";
import { RecordStartNote } from "../RecordStartNote";

// Idea Timeline (docs/dashboards/idea-timeline.md): each live card's RECORDED state over time on one shared
// date axis, with its dated catalysts, the logged decisions and the latest call's two clocks. A "now" view,
// display only. The reading of the record (segments, last row per night, active excludes, the axis cap) is
// computed server-side; this page lays it out and filters / sorts / zooms what is on screen.

const at = (v: View, iso: string) => ({ left: `${pct(v, dayNum(iso))}%` });

function Track({
  row,
  view,
  ticks,
  onDetail,
}: {
  row: IdeaTimelineRowOut;
  view: View;
  ticks: Tick[];
  onDetail: (text: string | null) => void;
}) {
  const todayLeft = pct(view, view.today);
  const current = row.segments.find((s) => s.current);
  // the entry window runs from the current armed run's first night to arm_until (a tick when it predates it)
  const entry =
    row.arm_until && current && current.state === "armed"
      ? spanStyle(
          view,
          current.start < row.arm_until ? current.start : row.arm_until,
          row.arm_until,
        )
      : null;
  const hover = (text: string) => ({
    onMouseEnter: () => onDetail(`${row.thesis_name} · ${text.replace(/\n/g, " · ")}`),
    onMouseLeave: () => onDetail(null),
  });
  return (
    <div className="it-track">
      {ticks.map((t) => (
        <span key={t.day} className="it-grid" style={{ left: `${t.left}%` }} />
      ))}
      {todayLeft < 100 && (
        <span className="it-future" style={{ left: `${Math.max(todayLeft, 0)}%` }} />
      )}
      {row.segments.map((s) => {
        const style = spanStyle(view, s.start, s.end);
        const title = segmentTitle(s);
        return (
          style && (
            <span
              key={s.start}
              className={`it-seg st-${stateCls(s.state)}${s.current ? " current" : ""}`}
              style={style}
              title={title}
              {...hover(title)}
            />
          )
        );
      })}
      {row.segments.flatMap((s) =>
        s.hatches.map((h) => {
          const style = spanStyle(view, h.start, h.end);
          return (
            style && (
              <span key={`${h.kind}:${h.start}`} className={`it-hatch ${h.kind}`} style={style} />
            )
          );
        }),
      )}
      {entry && row.arm_until && (
        <span
          className="it-entry"
          style={entry}
          title={entryTitle(row.arm_until)}
          {...hover(entryTitle(row.arm_until))}
        />
      )}
      {row.exit_by && inView(view, dayNum(row.exit_by)) && (
        <span
          className="it-exit"
          style={at(view, row.exit_by)}
          title={exitTitle(row.exit_by)}
          {...hover(exitTitle(row.exit_by))}
        />
      )}
      {row.catalysts
        .filter((c) => inView(view, dayNum(c.when_date)))
        .map((c, i) => {
          const title = catalystTitle(c);
          return (
            <span
              key={`${c.when_date}:${i}`}
              className={`it-cat${c.past ? " past" : ""}`}
              style={at(view, c.when_date)}
              title={title}
              {...hover(title)}
            />
          );
        })}
      {row.decisions
        .filter((d) => inView(view, dayNum(d.decision_date)))
        .map((d) => {
          const title = decisionTitle(d);
          return (
            <span
              key={d.id}
              className={`it-dec ${d.action}${d.voided ? " voided" : ""}`}
              style={at(view, d.decision_date)}
              title={title}
              {...hover(title)}
            >
              {DECISION_GLYPH[d.action]}
            </span>
          );
        })}
      {todayLeft >= 0 && todayLeft <= 100 && (
        <span className="it-today" style={{ left: `${todayLeft}%` }} />
      )}
    </div>
  );
}

const RIBBON_KEYS = [
  { key: "armed", cls: "st-armed", label: "Armed" },
  { key: "warming", cls: "st-warm", label: "Warming" },
  { key: "managing", cls: "st-manage", label: "Managing" },
  { key: "incubating", cls: "st-incub", label: "Incubating" },
] as const;

/** Stacked state-count history ribbon aligned to the same visible date window as the tracks below. */
function StateRibbon({
  series,
  view,
  ticks,
  onDetail,
}: {
  series: readonly DayStateCounts[];
  view: View;
  ticks: Tick[];
  onDetail: (text: string | null) => void;
}) {
  const maxTotal = Math.max(1, ...series.map((p) => p.total));
  if (series.length < 2 || series.every((p) => p.total === 0)) return null;
  const W = 1000;
  const H = 38;
  const padTop = 3;
  const usable = H - padTop - 2;
  const xOf = (d: number) => (pct(view, d) / 100) * W;
  const yOf = (v: number) => H - 1 - (v / maxTotal) * usable;

  const layers: { key: string; cls: string; d: string }[] = [];
  let prev = series.map(() => 0);
  for (const layer of RIBBON_KEYS) {
    const next = series.map((p, i) => prev[i] + p[layer.key]);
    const hasAny = series.some((p) => p[layer.key] > 0);
    if (hasAny) {
      const top = series.map((p, i) => `${i === 0 ? "M" : "L"}${xOf(p.day).toFixed(1)} ${yOf(next[i]).toFixed(1)}`).join(" ");
      const bot = [...series]
        .reverse()
        .map((p, ri) => {
          const i = series.length - 1 - ri;
          return `L${xOf(p.day).toFixed(1)} ${yOf(prev[i]).toFixed(1)}`;
        })
        .join(" ");
      layers.push({ key: layer.key, cls: layer.cls, d: `${top} ${bot} Z` });
    }
    prev = next;
  }

  const todayX = xOf(view.today);
  const last = series[series.length - 1];
  const describe = (p: DayStateCounts) => {
    const parts = RIBBON_KEYS.filter((k) => p[k.key] > 0).map((k) => `${p[k.key]} ${k.label}`);
    return `Workspace on ${fmtDay(p.iso)}: ${parts.length ? parts.join(" · ") : "no recorded state"}`;
  };

  return (
    <div className="it-ribbon" data-testid="it-ribbon" aria-hidden="true">
      <div className="it-ribbon-label">
        <span>State mix</span>
        <span className="it-ribbon-max">peak {maxTotal}</span>
      </div>
      <div className="it-ribbon-chart">
        <svg className="it-ribbon-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
          {todayX < W && (
            <rect className="it-ribbon-future" x={Math.max(0, todayX)} y={0} width={W - Math.max(0, todayX)} height={H} />
          )}
          {ticks.map((t) => (
            <line key={t.day} className="it-ribbon-grid" x1={(t.left / 100) * W} x2={(t.left / 100) * W} y1={0} y2={H} />
          ))}
          {layers.map((l) => (
            <path key={l.key} className={`it-ribbon-area ${l.cls}`} d={l.d} />
          ))}
          {todayX >= 0 && todayX <= W && (
            <line className="it-ribbon-today" x1={todayX} x2={todayX} y1={0} y2={H} />
          )}
          {series.map((p, i) => {
            const x0 = i === 0 ? xOf(p.day) : (xOf(series[i - 1].day) + xOf(p.day)) / 2;
            const x1 = i === series.length - 1 ? xOf(p.day) + 6 : (xOf(p.day) + xOf(series[i + 1].day)) / 2;
            return (
              <rect
                key={p.day}
                className="it-ribbon-hit"
                x={Math.max(0, x0)}
                y={0}
                width={Math.max(2, x1 - x0)}
                height={H}
                onMouseEnter={() => onDetail(describe(p))}
                onMouseLeave={() => onDetail(null)}
              />
            );
          })}
        </svg>
        <span className="it-ribbon-now" title={describe(last)}>
          today: {last.armed}A · {last.warming}W · {last.managing}M · {last.incubating}I
        </span>
      </div>
    </div>
  );
}

// a month label this close to today's (in percent of the axis) would print over it; its gridline still shows
const LABEL_GAP = 4;

function Axis({ view, ticks }: { view: View; ticks: Tick[] }) {
  const todayLeft = pct(view, view.today);
  return (
    <div className="it-axis">
      {ticks
        .filter((t) => Math.abs(t.left - todayLeft) >= LABEL_GAP)
        .map((t) => (
          <span key={t.day} className="it-tick" style={{ left: `${t.left}%` }}>
            {t.label}
          </span>
        ))}
      {todayLeft >= 0 && todayLeft <= 100 && (
        <span className="it-tick today" style={{ left: `${todayLeft}%` }}>
          today
        </span>
      )}
    </div>
  );
}

function Legend() {
  return (
    <div className="it-legend" aria-hidden="true">
      {STATES.filter((s) => s.key !== "none").map((s) => (
        <span key={s.key} className="it-lg">
          <span className={`it-sw st-${s.cls}`} />
          {s.label.toLowerCase()}
        </span>
      ))}
      <span className="it-lg">
        <span className="it-sw st-incub it-sw-hatch reconstructed" />
        reconstructed
      </span>
      <span className="it-lg">
        <span className="it-sw-cat" />
        catalyst
      </span>
      <span className="it-lg">
        {DECISION_GLYPH.take} take · {DECISION_GLYPH.close} close · {DECISION_GLYPH.pass} pass
      </span>
      <span className="it-lg">] exit-by</span>
      <span className="it-lg">
        <span className="it-sw-entry" />
        entry window
      </span>
    </div>
  );
}

export default function IdeaTimeline({ onSelectThesis }: DashboardProps) {
  const [zoom, setZoom] = useState<Zoom>("all");
  const [sort, setSort] = useState<SortKey>("state");
  const [states, setStates] = useState<ReadonlySet<string>>(new Set());
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState<string | null>(null);
  const q = useIdeaTimeline();
  const data = q.data;

  const view = useMemo(() => (data ? viewFor(zoom, data) : null), [zoom, data]);
  const ticks = useMemo(() => (view ? monthTicks(view) : []), [view]);
  const ribbon = useMemo(() => (data && view ? dailyStateCounts(data.rows, view) : []), [data, view]);

  if (q.isLoading) return <div className="dash-note">Loading…</div>;
  if (q.error || !data || !view) {
    return <div className="dash-note">Could not load the Idea Timeline.</div>;
  }

  const counts = new Map<string, number>();
  for (const r of data.rows) {
    counts.set(stateKey(r.current_state), (counts.get(stateKey(r.current_state)) ?? 0) + 1);
  }
  const query = search.trim().toLocaleLowerCase();
  const filtered = filterRows(data.rows, states).filter(
    (r) => !query || r.thesis_name.toLocaleLowerCase().includes(query),
  );
  const rows = sortRows(filtered, sort);
  const toggleState = (key: string) =>
    setStates((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const sorted = (key: SortKey) => (sort === key ? " ↓" : "");
  const sortTh = (key: SortKey, label: string, cls?: string) => (
    <th className={cls} aria-sort={sort === key ? "descending" : "none"}>
      <button type="button" className={`it-th-btn${sort === key ? " on" : ""}`} onClick={() => setSort(key)}>
        {label}
        {sorted(key)}
      </button>
    </th>
  );

  return (
    <div className="it">
      <RecordStartNote empty={data.recorded_calls === 0} />
      <div className="it-controls">
        <div className="it-group" role="group" aria-label="Zoom">
          {ZOOMS.map((z) => (
            <button
              key={z.key}
              type="button"
              className={`it-chip${zoom === z.key ? " on" : ""}`}
              aria-pressed={zoom === z.key}
              onClick={() => setZoom(z.key)}
            >
              {z.label}
            </button>
          ))}
        </div>
        <div className="it-group" role="group" aria-label="Show states">
          {/* a chip renders only for a state some card holds (honest loudness, WB#3) */}
          {STATES.filter((s) => (counts.get(s.key) ?? 0) > 0 || states.has(s.key)).map((s) => (
            <button
              key={s.key}
              type="button"
              className={`it-chip it-state-chip st-${s.cls}${states.has(s.key) ? " on" : ""}`}
              aria-pressed={states.has(s.key)}
              title={`Show only ${s.label.toLowerCase()} cards (with any other chosen states)`}
              onClick={() => toggleState(s.key)}
            >
              {s.label} · {counts.get(s.key) ?? 0}
            </button>
          ))}
        </div>
        <label className="it-sort">
          Sort
          <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
            {SORTS.map((s) => (
              <option key={s.key} value={s.key}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        {data.rows.length > 1 && (
          <input
            type="search"
            className="it-search"
            aria-label="Filter cards"
            placeholder="Filter cards…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        )}
        {q.isFetching && <span className="dash-note">updating…</span>}
      </div>
      <div className="dash-note it-work">{workLine(data)}</div>
      <div className="it-meta-row">
        <Legend />
        <div className="it-detail" data-testid="it-detail" aria-live="polite">
          {detail ?? "Hover any state bar, catalyst, decision, or clock for its detail"}
        </div>
      </div>
      <StateRibbon series={ribbon} view={view} ticks={ticks} onDetail={setDetail} />

      {data.rows.length === 0 ? (
        <div className="dash-note">No live cards.</div>
      ) : rows.length === 0 ? (
        <div className="dash-note">No card matches these filters.</div>
      ) : (
        <div className="it-scroll">
          <table className="it-table">
            <thead>
              <tr>
                {sortTh("name", "Card", "it-card")}
                {sortTh("state", "State")}
                {sortTh("days_in_state", "Days in state", "it-num")}
                {sortTh("days_armed", "Days armed", "it-num")}
                {sortTh("times_armed", "Times armed", "it-num")}
                {sortTh("first_recorded", "First recorded")}
                <th className="it-line">
                  <Axis view={view} ticks={ticks} />
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const later = markersLater(r, view);
                const notes = [
                  r.undated_catalysts > 0 ? `+${r.undated_catalysts} undated` : null,
                  later > 0 ? `+${later} later` : null,
                ].filter(Boolean);
                return (
                  <tr key={r.thesis_id}>
                    <td className="it-card">
                      <button
                        type="button"
                        className="it-name"
                        title={`Open ${r.thesis_name} in the Cockpit`}
                        onClick={() => onSelectThesis(r.thesis_id)}
                      >
                        {r.thesis_name}
                      </button>
                      {notes.length > 0 && (
                        <span
                          className="it-rownote"
                          title={[
                            r.undated_catalysts > 0
                              ? `${plural(r.undated_catalysts, "catalyst", "catalysts")} with no date (not on the axis)`
                              : null,
                            later > 0
                              ? `${plural(later, "marker", "markers")} past the visible end of the axis`
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                        >
                          {notes.join(" · ")}
                        </span>
                      )}
                    </td>
                    <td>
                      <span
                        className={`it-state st-${stateCls(r.current_state)}`}
                        title={
                          r.current_verdict
                            ? `Latest recorded verdict: ${verdictLabel(r.current_verdict)}`
                            : "No recorded call yet"
                        }
                      >
                        {stateLabel(r.current_state)}
                      </span>
                    </td>
                    <td className="it-num">{r.days_in_state ?? "—"}</td>
                    <td className="it-num">{r.total_days_armed}</td>
                    <td className="it-num">{r.times_armed}</td>
                    <td className="it-date">{fmtDate(r.first_recorded)}</td>
                    <td className="it-line">
                      <Track row={r} view={view} ticks={ticks} onDetail={setDetail} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
