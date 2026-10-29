import { useMemo, useState } from "react";

import { fmtDate, verdictLabel } from "../../util/format";
import type { DashboardProps } from "../registry";
import "./idea-timeline.css";
import {
  catalystTitle,
  DECISION_GLYPH,
  decisionTitle,
  dayNum,
  entryTitle,
  exitTitle,
  filterRows,
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
  type SortKey,
  type Tick,
  type View,
  type Zoom,
} from "./timeline";
import { useIdeaTimeline, type IdeaTimelineRowOut } from "./useIdeaTimeline";

// Idea Timeline (docs/dashboards/idea-timeline.md): each live card's RECORDED state over time on one shared
// date axis, with its dated catalysts, the logged decisions and the latest call's two clocks. A "now" view,
// display only. The reading of the record (segments, last row per night, active excludes, the axis cap) is
// computed server-side; this page lays it out and filters / sorts / zooms what is on screen.

const at = (v: View, iso: string) => ({ left: `${pct(v, dayNum(iso))}%` });

function Track({ row, view, ticks }: { row: IdeaTimelineRowOut; view: View; ticks: Tick[] }) {
  const todayLeft = pct(view, view.today);
  const current = row.segments.find((s) => s.current);
  // the entry window runs from the current run's first night to arm_until (a tick when it predates it)
  const entry =
    row.arm_until && current
      ? spanStyle(
          view,
          current.start < row.arm_until ? current.start : row.arm_until,
          row.arm_until,
        )
      : null;
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
        return (
          style && (
            <span
              key={s.start}
              className={`it-seg st-${stateCls(s.state)}${s.current ? " current" : ""}`}
              style={style}
              title={segmentTitle(s)}
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
        <span className="it-entry" style={entry} title={entryTitle(row.arm_until)} />
      )}
      {row.exit_by && inView(view, dayNum(row.exit_by)) && (
        <span className="it-exit" style={at(view, row.exit_by)} title={exitTitle(row.exit_by)} />
      )}
      {row.catalysts
        .filter((c) => inView(view, dayNum(c.when_date)))
        .map((c, i) => (
          <span
            key={`${c.when_date}:${i}`}
            className={`it-cat${c.past ? " past" : ""}`}
            style={at(view, c.when_date)}
            title={catalystTitle(c)}
          />
        ))}
      {row.decisions
        .filter((d) => inView(view, dayNum(d.decision_date)))
        .map((d) => (
          <span
            key={d.id}
            className={`it-dec ${d.action}${d.voided ? " voided" : ""}`}
            style={at(view, d.decision_date)}
            title={decisionTitle(d)}
          >
            {DECISION_GLYPH[d.action]}
          </span>
        ))}
      {todayLeft >= 0 && todayLeft <= 100 && (
        <span className="it-today" style={{ left: `${todayLeft}%` }} />
      )}
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
        <span className="it-sw st-incub it-sw-hatch excluded" />
        set aside
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
  const q = useIdeaTimeline();
  const data = q.data;

  const view = useMemo(() => (data ? viewFor(zoom, data) : null), [zoom, data]);
  const ticks = useMemo(() => (view ? monthTicks(view) : []), [view]);

  if (q.isLoading) return <div className="dash-note">Loading…</div>;
  if (q.error || !data || !view) {
    return <div className="dash-note">Could not load the Idea Timeline.</div>;
  }

  const counts = new Map<string, number>();
  for (const r of data.rows) {
    counts.set(stateKey(r.current_state), (counts.get(stateKey(r.current_state)) ?? 0) + 1);
  }
  const rows = sortRows(filterRows(data.rows, states), sort);
  const toggleState = (key: string) =>
    setStates((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  const sorted = (key: SortKey) => (sort === key ? " ↓" : "");

  return (
    <div className="it">
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
        {q.isFetching && <span className="dash-note">updating…</span>}
      </div>
      <div className="dash-note it-work">{workLine(data)}</div>
      <Legend />

      {data.rows.length === 0 ? (
        <div className="dash-note">No live cards.</div>
      ) : rows.length === 0 ? (
        <div className="dash-note">No card matches these filters.</div>
      ) : (
        <table className="it-table">
          <thead>
            <tr>
              <th className="it-card">Card{sorted("name")}</th>
              <th>State{sorted("state")}</th>
              <th className="it-num">Days in state{sorted("days_in_state")}</th>
              <th className="it-num">Days armed{sorted("days_armed")}</th>
              <th className="it-num">Times armed</th>
              <th>First recorded{sorted("first_recorded")}</th>
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
                    <Track row={r} view={view} ticks={ticks} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
