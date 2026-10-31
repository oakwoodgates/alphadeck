import { Fragment, useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";

import { STATE_LABEL } from "../../util/format";
import { FlaggedNote } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./entry-windows.css";
import type {
  EntryWindowsMemberOut,
  EntryWindowsOut,
  EntryWindowsRowOut,
  EntryWindowsSourceOut,
} from "./useEntryWindows";
import { useEntryWindows } from "./useEntryWindows";
import {
  AXIS_H,
  BAR_H,
  ENTRY_Y,
  HOLD_Y,
  PIC_W,
  ROW_H,
  TICK_R,
  TICK_Y,
  axisTicks,
  layoutRow,
  makeAxis,
  moveRow,
  type Axis,
  type RowDir,
  type RowLayout,
} from "./windowsLayout";
import {
  CLOSING_TITLE,
  DEFAULT_SORT,
  HOLD_TAG,
  SORT_OPTIONS,
  cardHoldClosing,
  catalystWords,
  fmtDay,
  holdWords,
  intoWords,
  leftWords,
  memberEntryLines,
  memberHoldLines,
  memberSinceWords,
  plural,
  rowWords,
  shownRows,
  sinceCell,
  sortRows,
  tone,
  triggerWords,
  workLine,
  type SortKey,
} from "./windowsView";

// Entry Windows (docs/dashboards/entry-windows.md): one row per Armed card, its entry window (to arm_until) and
// hold horizon (to exit_by) drawn from the arm night on ONE date axis with the as-of through every row, the
// sessions into and left in the window, the dated catalysts inside it, and what the armed names have done since
// the arm night against IWM and SPY. Display only. Every number is computed server-side; this page orders the
// rows, lays the picture out (inline SVG from the pure windowsLayout.ts) and words it. The row list is one tab
// stop and the arrow keys move between rows; a hover or a focus fills the one readout line.

const PROMPT =
  "Hover a bar, a dot or the line, or focus a row (the arrow keys move between rows), for its dates. Click a row to open its names.";
const ARROWS: Record<string, RowDir> = { ArrowUp: "up", ArrowDown: "down", Home: "home", End: "end" };

type Hover = (text: string | null) => void;

// --- the picture -------------------------------------------------------------------------------------------------

function Picture({
  row,
  layout,
  axis,
  onHover,
}: {
  row: EntryWindowsRowOut;
  layout: RowLayout;
  axis: Axis;
  onHover: Hover;
}) {
  const grid = useMemo(() => axisTicks(axis), [axis]);
  const entryText = `Entry window: ${fmtDay(row.arm_night)} → ${fmtDay(row.arm_until)} · ${leftWords(row)}`;
  const holdText = `Hold horizon: ${fmtDay(row.arm_night)} → ${fmtDay(row.exit_by)} · ${holdWords(row)}`;
  const spark = row.spark;
  const sparkText = spark
    ? `Armed names' equal-weighted line ${spark.line[spark.line.length - 1].toFixed(1)}${
        spark.iwm ? ` against IWM ${spark.iwm[spark.iwm.length - 1].toFixed(1)}` : ""
      }, both from 100 on ${fmtDay(spark.dates[0])}`
    : null;
  const hover = (text: string) => ({ onMouseEnter: () => onHover(text), onMouseLeave: () => onHover(null) });
  return (
    <svg
      className="ew-svg"
      role="img"
      aria-label={rowWords(row)}
      width={PIC_W}
      height={ROW_H}
      viewBox={`0 0 ${PIC_W} ${ROW_H}`}
    >
      {grid.map((t) => (
        <line key={t.iso} className="ew-grid" x1={t.x} x2={t.x} y1={0} y2={ROW_H} />
      ))}
      {layout.entry && (
        <rect
          className="ew-bar entry"
          data-testid="ew-entry"
          x={layout.entry.x}
          y={ENTRY_Y}
          width={layout.entry.w}
          height={BAR_H}
          {...hover(entryText)}
        >
          <title>{entryText}</title>
        </rect>
      )}
      {layout.hold && (
        <rect
          className="ew-bar hold"
          data-testid="ew-hold"
          x={layout.hold.x}
          y={HOLD_Y}
          width={layout.hold.w}
          height={BAR_H}
          {...hover(holdText)}
        >
          <title>{holdText}</title>
        </rect>
      )}
      <line className="ew-arm" x1={layout.armX} x2={layout.armX} y1={ENTRY_Y - 2} y2={HOLD_Y + BAR_H + 2} />
      {layout.ticks.map((t, i) => {
        const text = catalystWords({ label: t.label, kind: t.kind, when_date: t.when, past: t.past });
        return (
          <circle
            key={`${t.when}-${i}`}
            className={`ew-tick${t.past ? " past" : ""}`}
            data-testid="ew-tick"
            cx={t.x}
            cy={TICK_Y}
            r={TICK_R}
            {...hover(text)}
          >
            <title>{text}</title>
          </circle>
        );
      })}
      {layout.spark && (
        <g {...(sparkText ? hover(sparkText) : {})}>
          {layout.spark.base != null && (
            <line className="ew-base" x1={layout.armX} x2={PIC_W} y1={layout.spark.base} y2={layout.spark.base} />
          )}
          {layout.spark.iwm && <path className="ew-iwm" data-testid="ew-iwm" d={layout.spark.iwm} />}
          <path className="ew-line" data-testid="ew-line" d={layout.spark.line} />
          {sparkText && <title>{sparkText}</title>}
        </g>
      )}
      <line className="ew-asof" data-testid="ew-asof" x1={layout.asofX} x2={layout.asofX} y1={0} y2={ROW_H} />
    </svg>
  );
}

function AxisHeader({ axis, asof }: { axis: Axis; asof: string }) {
  const ticks = useMemo(() => axisTicks(axis), [axis]);
  const x = axis.x(asof);
  return (
    <svg
      className="ew-svg"
      role="img"
      aria-label={`Date axis, ${fmtDay(asof)} marked`}
      width={PIC_W}
      height={AXIS_H}
      viewBox={`0 0 ${PIC_W} ${AXIS_H}`}
    >
      {ticks.map((t) => (
        <g key={t.iso}>
          <line className="ew-grid" x1={t.x} x2={t.x} y1={AXIS_H - 5} y2={AXIS_H} />
          <text className="ew-axis-label" x={t.x} y={AXIS_H - 8} textAnchor="middle">
            {t.label}
          </text>
        </g>
      ))}
      <line className="ew-asof" x1={x} x2={x} y1={0} y2={AXIS_H} />
      <text className="ew-asof-label" x={x > PIC_W - 70 ? x - 4 : x + 4} y={9} textAnchor={x > PIC_W - 70 ? "end" : "start"}>
        as of {fmtDay(asof)}
      </text>
    </svg>
  );
}

// --- a row -------------------------------------------------------------------------------------------------------

const stop = (e: MouseEvent) => e.stopPropagation();

function SourceLink({ s }: { s: EntryWindowsSourceOut }) {
  const text = `${s.source} ${s.ref}`;
  return s.url ? (
    <a className="ew-src" href={s.url} target="_blank" rel="noopener noreferrer" onClick={stop}>
      {text}
    </a>
  ) : (
    <span className="ew-src">{text}</span>
  );
}

function Member({
  m,
  onOpen,
}: {
  m: EntryWindowsMemberOut;
  onOpen: (m: EntryWindowsMemberOut) => void;
}) {
  return (
    <li className={`ew-member${m.flag ? " flagged" : ""}`} data-testid="ew-member">
      <div className="ew-member-head">
        <button type="button" className="ew-chip" title={`Open ${m.ticker ?? "this name"} in the Cockpit`} onClick={() => onOpen(m)}>
          {m.ticker ?? "?"}
        </button>
        <span className="ew-n">armed since {fmtDay(m.arm_night)}</span>
        <span className={`ew-fig-n ${m.flag ? "" : tone(m.since)}`}>{memberSinceWords(m)}</span>
      </div>
      {m.triggers.length === 0 ? (
        <div className="ew-trig ew-n">no trigger recorded on this name</div>
      ) : (
        <ul className="ew-trigs">
          {m.triggers.map((t, i) => (
            <li key={i} className="ew-trig">
              {triggerWords(t)}
              {t.sources.map((s, j) => (
                <Fragment key={j}> · <SourceLink s={s} /></Fragment>
              ))}
              {t.sources.length === 0 && <span className="ew-n"> · no source stated</span>}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function RowView({
  row,
  layout,
  axis,
  open,
  tabbable,
  onToggle,
  onHover,
  onFocusRow,
  onBlurRow,
  onKey,
  onSelectThesis,
}: {
  row: EntryWindowsRowOut;
  layout: RowLayout;
  axis: Axis;
  open: boolean;
  tabbable: boolean;
  onToggle: (key: string) => void;
  onHover: Hover;
  onFocusRow: (key: string) => void;
  onBlurRow: (key: string) => void;
  onKey: (e: KeyboardEvent<HTMLTableRowElement>, key: string) => void;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const s = sinceCell(row.since);
  const closing = row.entry_window_closing;
  return (
    <>
      <tr
        className={`ew-row ${row.state}${open ? " open" : ""}`}
        data-testid="ew-row"
        data-key={row.thesis_id}
        tabIndex={tabbable ? 0 : -1}
        aria-expanded={open}
        onClick={() => onToggle(row.thesis_id)}
        onKeyDown={(e) => onKey(e, row.thesis_id)}
        onFocus={(e) => e.target === e.currentTarget && onFocusRow(row.thesis_id)}
        onBlur={(e) => e.target === e.currentTarget && onBlurRow(row.thesis_id)}
        onMouseEnter={() => onHover(rowWords(row))}
        onMouseLeave={() => onHover(null)}
      >
        <td className="ew-card">
          <div className="ew-card-head">
            <button
              type="button"
              className="ew-card-name"
              title={`Open ${row.thesis_name} in the Cockpit`}
              onClick={(e) => {
                e.stopPropagation();
                onSelectThesis(row.thesis_id);
              }}
            >
              {row.thesis_name}
            </button>
            <span className={`ew-state ${row.state}`}>{STATE_LABEL[row.state]}</span>
            {row.confidence != null && (
              <span className="ew-n" title="An experimental relative indicator, not a probability">
                setup strength {row.confidence.toFixed(2)}
              </span>
            )}
            {row.reconstructed && (
              <span className="ew-n" title="The arm night's row was written by pipeline.backfill: reported, never scored">
                reconstructed
              </span>
            )}
          </div>
          <div className="ew-chips">
            {row.members.length === 0 && <span className="ew-n">no armed name recorded</span>}
            {row.members.map((m) => (
              <button
                key={m.security_id}
                type="button"
                className={`ew-chip${m.flag ? " flagged" : ""}`}
                title={m.flag ? `${m.ticker ?? "?"} — ${memberSinceWords(m)}` : `Open ${m.ticker ?? "this name"} in the Cockpit`}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelectThesis(row.thesis_id, m.ticker ?? m.security_id);
                }}
              >
                {m.ticker ?? "?"}
              </button>
            ))}
          </div>
        </td>
        <td className="ew-pic">
          <Picture row={row} layout={layout} axis={axis} onHover={onHover} />
        </td>
        <td className="ew-fig ew-into" data-label="Sessions in" title={`IWM sessions after the arm night (${fmtDay(row.arm_night)}) through the as-of`}>
          <div className="ew-main">{intoWords(row)}</div>
          <div className="ew-sub">armed {fmtDay(row.arm_night)}</div>
        </td>
        <td className="ew-fig ew-left" data-label="Entry window">
          <div className={`ew-main${closing ? " closing" : ""}`} title={closing ? CLOSING_TITLE : undefined} data-testid="ew-left">
            {leftWords(row)}
          </div>
          <div className="ew-sub">{row.arm_until ? `to ${fmtDay(row.arm_until)}` : ""}</div>
          {memberEntryLines(row).map((line, i) => (
            <div key={i} className="ew-sub" data-testid="ew-member-entry">
              {line}
            </div>
          ))}
        </td>
        <td className="ew-fig ew-hold" data-label="Hold horizon">
          <div className="ew-main" data-testid="ew-hold-days">
            {holdWords(row)}
          </div>
          <div className="ew-sub">
            {row.exit_by ? `to ${fmtDay(row.exit_by)}` : ""}
            {cardHoldClosing(row) && <span className="ew-tag"> {HOLD_TAG}</span>}
          </div>
          {memberHoldLines(row).map((line, i) => (
            <div key={i} className="ew-sub" data-testid="ew-member-hold">
              {line}
            </div>
          ))}
        </td>
        <td className="ew-fig ew-since" data-label="Since the arm night">
          <div className={`ew-main ${s.tone}`}>
            {s.main} <span className="ew-n">{s.basis}</span>
          </div>
          <div className="ew-sub">
            {s.iwm} · {s.spy}
          </div>
          {s.above && <div className="ew-sub">{s.above}</div>}
          {s.unpriced && <div className="ew-sub">{s.unpriced} out of the median</div>}
        </td>
      </tr>
      {open && (
        <tr className="ew-fold" data-testid="ew-fold">
          <td colSpan={6}>
            <ul className="ew-members">
              {row.members.map((m) => (
                <Member key={m.security_id} m={m} onOpen={(mm) => onSelectThesis(row.thesis_id, mm.ticker ?? mm.security_id)} />
              ))}
            </ul>
            {row.catalysts.length > 0 && (
              <ul className="ew-cats">
                {row.catalysts.map((c, i) => (
                  <li key={i} className="ew-n">
                    {catalystWords(c)}
                  </li>
                ))}
              </ul>
            )}
            <div className="ew-fine">
              Recorded {plural(row.run_nights, "night", "nights")} in the run, the card shown is the {fmtDay(row.in_effect)} night
              {row.reconstructed_nights > 0 ? `, ${row.reconstructed_nights} of them reconstructed` : ""}. A name's own arm
              night is the first night of the run the recorded card names it armed.
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// --- the page ----------------------------------------------------------------------------------------------------

function Page({
  data,
  onSelectThesis,
}: {
  data: EntryWindowsOut;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const [showManaging, setShowManaging] = useState(false);
  const [sort, setSort] = useState<SortKey>(DEFAULT_SORT);
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const [active, setActive] = useState<string | null>(null);
  const [focused, setFocused] = useState<string | null>(null);
  const [hover, setHover] = useState<string | null>(null);

  const rows = useMemo(() => sortRows(shownRows(data.rows, showManaging), sort), [data.rows, showManaging, sort]);
  const axis = useMemo(
    () => (data.axis_start && data.axis_end ? makeAxis(data.axis_start, data.axis_end) : null),
    [data.axis_start, data.axis_end],
  );
  const layouts = useMemo(
    () => (axis ? new Map(rows.map((r) => [r.thesis_id, layoutRow(r, axis, data.asof)] as const)) : new Map<string, RowLayout>()),
    [rows, axis, data.asof],
  );
  const keys = useMemo(() => rows.map((r) => r.thesis_id), [rows]);
  const tab = active != null && keys.includes(active) ? active : (keys[0] ?? null);
  const focusedRow = focused != null ? rows.find((r) => r.thesis_id === focused) : undefined;
  const readout = hover ?? (focusedRow ? rowWords(focusedRow) : null);

  const toggle = (key: string) =>
    setOpen((cur) => {
      const next = new Set(cur);
      if (!next.delete(key)) next.add(key);
      return next;
    });
  const focusKey = (key: string) =>
    document.querySelector<HTMLElement>(`[data-testid="ew-row"][data-key="${key}"]`)?.focus();
  const onKey = (e: KeyboardEvent<HTMLTableRowElement>, key: string) => {
    if (e.target !== e.currentTarget) return; // a button or link inside the row keeps its own keys
    const dir = ARROWS[e.key];
    if (dir) {
      e.preventDefault();
      const next = moveRow(keys, key, dir);
      setActive(next);
      focusKey(next);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      toggle(key);
    }
  };

  const c = data.counts;
  return (
    <div className="ew">
      <div className="ew-controls">
        {c.managing > 0 && (
          <label className="ew-check" title="A Managing card's hold horizon still runs; its window is drawn from the Armed run before the position">
            <input type="checkbox" checked={showManaging} onChange={(e) => setShowManaging(e.target.checked)} />
            Show Managing ({c.managing.toLocaleString("en-US")})
          </label>
        )}
        {rows.length > 1 && (
          <label className="ew-sort">
            <span className="ew-seg-label">Order</span>
            <select
              className="ew-select"
              aria-label="Order the rows"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
            >
              {SORT_OPTIONS.map((o) => (
                <option key={o.key} value={o.key}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>

      <div className="ew-readout" data-testid="ew-readout" aria-live="polite">
        {readout ?? <span className="ew-prompt">{PROMPT}</span>}
      </div>

      {rows.length === 0 || !axis ? (
        <div className="dash-note" data-testid="ew-empty">
          {c.armed === 0 && c.managing === 0
            ? "No card is Armed at this as-of."
            : "No Armed row at this as-of; the Managing rows are behind the toggle above."}
        </div>
      ) : (
        <div className="ew-scroll">
          <table className="ew-table">
            <thead>
              <tr>
                <th className="ew-th-card">Card</th>
                <th className="ew-th-pic">
                  <AxisHeader axis={axis} asof={data.asof} />
                </th>
                <th className="ew-th-fig" title="IWM sessions after the arm night through the as-of">
                  Sessions in
                </th>
                <th className="ew-th-fig" title="The sessions after the as-of through arm_until (Mon-Fri days)">
                  Entry window
                </th>
                <th className="ew-th-fig" title="Calendar days from the as-of to exit_by">
                  Hold horizon
                </th>
                <th className="ew-th-fig" title="As traded, from the first close on or after the arm night to the last close on or before the as-of">
                  Since the arm night
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => {
                const layout = layouts.get(r.thesis_id);
                if (!layout) return null;
                return (
                  <RowView
                    key={r.thesis_id}
                    row={r}
                    layout={layout}
                    axis={axis}
                    open={open.has(r.thesis_id)}
                    tabbable={r.thesis_id === tab}
                    onToggle={toggle}
                    onHover={setHover}
                    onFocusRow={(k) => {
                      setActive(k);
                      setFocused(k);
                    }}
                    onBlurRow={(k) => setFocused((cur) => (cur === k ? null : cur))}
                    onKey={onKey}
                    onSelectThesis={onSelectThesis}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <div className="dash-note ew-work" data-testid="ew-work">
        {workLine(data, showManaging)}
      </div>
      <FlaggedNote flagged={data.flagged} />
      <div className="dash-note ew-legend">
        Both bars start at the arm night: the upper is the entry window (to arm_until), the lower the hold horizon (to
        exit_by). The blue line is the as-of. A dot is a dated catalyst of the card inside the window, hollow once its
        date has passed. Below, the armed names' equal-weighted line (solid) and IWM (dashed), both from 100 at the arm
        night.
      </div>
      <ul className="ew-limits">
        <li>
          The arm night is the recorded night (when the card was first recorded Armed in its current run), not a trigger's
          fire date; the fold shows both.
        </li>
        <li>
          Sessions are IWM's bars. Sessions after the as-of are counted Mon-Fri without the NYSE's market holidays (the
          call path's own count).
        </li>
        <li>Medians are equal-weight over the names with a bar; returns are as-traded closes, with no dividends.</li>
        <li>
          The entry rule is the first close on or after the arm night, the night's own close: one the operator could not
          have taken. The Track Record page opens on the next session's close.
        </li>
        <li>
          Loudness marks the exception: the one accent on this page marks a card in which any armed name's entry window
          has at most two sessions left. A name whose window differs from the card's is named under the figure.
        </li>
      </ul>
    </div>
  );
}

export default function EntryWindows({ asof, onSelectThesis }: DashboardProps) {
  const q = useEntryWindows(asof);
  const data = q.data;
  if (q.isLoading || (!data && q.isFetching)) {
    return <div className="dash-note">Reading each card's recorded call and its names' tape — the first load after a run or a basket edit can take a moment.</div>;
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Entry Windows.</div>;
  const stale = data.asof !== asof;
  return (
    <>
      {q.isFetching && stale && <div className="dash-note">updating…</div>}
      <Page key={data.asof} data={data} onSelectThesis={onSelectThesis} />
    </>
  );
}
