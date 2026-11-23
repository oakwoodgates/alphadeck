import { memo, useCallback, useMemo, useState } from "react";

import { FlaggedNote, flaggedRule } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./hot-board.css";
import { HEAT_PARTS, heatByKey, type Heat } from "./heat";
import {
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  DEFAULT_TOGGLES,
  FORMULA,
  KIND_TAG,
  LIMITS,
  TOGGLE_KINDS,
  armNightCell,
  capCell,
  exitCell,
  filterControls,
  filtersActive,
  heatText,
  heatTitle,
  insiderCell,
  keysWords,
  kindWord,
  leadingMark,
  leftCell,
  nextSort,
  plural,
  riskCell,
  rowMatches,
  rowsOnShow,
  sinceArmCells,
  sinceCell,
  sortRows,
  sparkGeometry,
  strengthWords,
  tapeCells,
  tierLabel,
  toggleCounts,
  triggerLine,
  verdictWords,
  workLines,
  type Cell,
  type Filters,
  type GradePick,
  type Sort,
  type SortKey,
  type Toggles,
  type TierPick,
} from "./hotView";
import { useHotBoard, type HotRowOut } from "./useHotBoard";
import { ExternalLink } from "../../components/ExternalLink";
import { RecordStartNote } from "../RecordStartNote";

// Hot Board (docs/dashboards/hot-board.md): every name the RECORD shows Armed, one row each, ranked by a stated heat
// formula. A tape, not a document: dense rows, tabular numerals in the mono font, a sticky header, no cards and
// no chrome. Display only. Every figure is computed server-side from the calls log and the display members; this
// page picks the rows (the toggles and the filters), recomputes the heat among the rows it shows (heat.ts), sorts and
// words them. Loudness marks the exception: the sign tone only on a signed return, the Board's state color only on a
// state chip, ONE accent for an entry window with at most two sessions left (#7 / WB#3).
//
// Two reads. The default read carries the armed rows with their tape and only the COUNTS of the warming, watch and
// managing names, so the toggles can show "+N" without the cost of their tape. The first time a toggle is checked
// the page asks for the full read (`rows=all`) once; from then on the toggles, filters, sorts and heat are client
// side as before. Unchecking every toggle returns to the armed read, and the full read stays cached for the next check.

const NO_COUNTS = { warming: 0, watch: 0, managing: 0 };

type Column = { key: SortKey | null; label: string; title: string; num?: boolean; cls?: string; gstart?: boolean };

const COLUMNS: readonly Column[] = [
  { key: "card", label: "Card", title: "Sort by the card's name" },
  { key: "heat", label: "heat (stated formula)", title: "Sort by heat: the mean of four percentiles among the rows shown", num: true },
  { key: "verdict", label: "Call", title: "The member's verdict on the recorded call (a Warming name: which key its trigger turned)", gstart: true },
  { key: "grade", label: "Grade", title: "The weaker key's grade on the recorded call: flip or core" },
  { key: "strength", label: "Setup strength", title: "The call's setup strength: an experimental relative indicator, not a probability", num: true },
  { key: "keys", label: "Keys", title: "The entry triggers that fired. Hover for each one's label, grade, fire date and source" },
  { key: "risk", label: "Risk", title: "Risk signals on the card that name this security", num: true },
  { key: "arm", label: "Arm night", title: "The first night of the member's current recorded run on this card (R: a night rebuilt by the backfill)", num: true, gstart: true },
  { key: "since", label: "Sess. since", title: "Sessions after the arm night through the as-of, on IWM's bar calendar", num: true },
  { key: "left", label: "Sess. left", title: "Sessions left in the entry window after the as-of (closed N ago past it)", num: true },
  { key: "exit", label: "To exit-by", title: "Calendar days from the as-of to the hold horizon (exit_by)", num: true },
  { key: "ret", label: "Since arm", title: "As-traded return from the first close on or after the arm night to the as-of", num: true, gstart: true },
  { key: "iwm", label: "IWM", title: "IWM over the same dates", num: true },
  { key: "rvol", label: "RVOL 20", title: "The as-of bar's volume over the mean of the 20 bars before it", num: true, gstart: true },
  { key: "vs50", label: "vs 50d", title: "Close against its 50-day average", num: true },
  { key: "offHigh", label: "Off 52w high", title: "Close against the 52-week closing high", num: true },
  { key: "vsIwm", label: "vs IWM 30D", title: "The 30-trading-day return minus IWM's, in percentage points. A mark means relative strength is at a 13-week high", num: true },
  { key: "ret7", label: "7D", title: "Return over the last 7 trading days", num: true },
  { key: "ret30", label: "30D", title: "Return over the last 30 trading days", num: true },
  { key: "insider", label: "Ins 30d", title: "Open-market insider buys / distinct buyers over the last 30 days", num: true },
  { key: "adv", label: "$vol 20d", title: "Average dollar volume over the last 20 bars", num: true, gstart: true },
  { key: "cap", label: "Cap ≈", title: "Approximate market cap: the latest close times the latest shares fact", num: true },
  { key: null, label: "90d", title: "The last 90 closes (tick marks arm night when in range)" },
];

const SLOW =
  "Reading every counted card's recorded call and the tape of each armed name — the first load after a run or a basket edit can take a moment.";

function SortTh({ col, sort, onSort }: { col: Column; sort: Sort; onSort: (key: SortKey) => void }) {
  const cls = [col.num ? "hb-num" : "", col.gstart ? "hb-gstart" : ""].filter(Boolean).join(" ") || undefined;
  if (col.key === null) {
    return (
      <th className={cls} title={col.title}>
        {col.label}
      </th>
    );
  }
  const on = sort.key === col.key;
  return (
    <th className={cls} aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className={`hb-sort${on ? " on" : ""}`} title={col.title} onClick={() => onSort(col.key as SortKey)}>
        {col.label}
        {on && (
          <span className="hb-arrow" aria-hidden="true">
            {sort.dir === "asc" ? " ↑" : " ↓"}
          </span>
        )}
      </button>
    </th>
  );
}

function CellTd({
  cell,
  mark,
  gstart,
  lapsing,
}: {
  cell: Cell;
  mark?: { text: string; title: string } | null;
  gstart?: boolean;
  lapsing?: boolean;
}) {
  const cls = ["hb-cell", cell.tone ?? "", cell.accent ? "accent" : "", cell.gray ? "gray" : "", lapsing ? "lapsing" : ""]
    .filter(Boolean)
    .join(" ");
  return (
    <td className={`hb-num${gstart ? " hb-gstart" : ""}`}>
      <span className={cls} title={cell.title}>
        {cell.text}
      </span>
      {mark && (
        <span className="hb-mark" title={mark.title}>
          {mark.text}
        </span>
      )}
    </td>
  );
}

/** The last 90 closes as a path in a small box, with endpoint dot and optional arm-night tick; blank when <2 closes. */
function Spark({ values, sessionsSince }: { values: ReadonlyArray<number | null>; sessionsSince: number | null }) {
  const geo = sparkGeometry(values, 74, 16, sessionsSince);
  if (!geo) return <span className="hb-cell gray">—</span>;
  return (
    <svg
      className={`hb-spark${geo.tone ? ` ${geo.tone}` : ""}`}
      width={74}
      height={16}
      viewBox="0 0 74 16"
      role="img"
      aria-label="The last 90 closes"
    >
      {geo.armX !== null && <line className="hb-spark-arm" x1={geo.armX} x2={geo.armX} y1={1} y2={15} />}
      <path d={geo.d} fill="none" strokeWidth={1.15} />
      <circle className="hb-spark-end" cx={geo.endX} cy={geo.endY} r={1.6} />
    </svg>
  );
}

function KeysCell({ r, flipUp }: { r: HotRowOut; flipUp: boolean }) {
  if (r.triggers.length === 0) {
    return (
      <td>
        <span className="hb-cell gray">—</span>
      </td>
    );
  }
  return (
    <td className="hb-keys-td">
      <span
        className={`hb-keys${flipUp ? " flip-up" : ""}`}
        tabIndex={0}
        aria-label={`${keysWords(r)}: ${r.triggers.map(triggerLine).join("; ")}`}
      >
        {keysWords(r)}
        <span className="hb-tip" role="tooltip">
          {r.triggers.map((t, i) => (
            <span className="hb-tip-row" key={`${t.kind}:${t.event_date}:${i}`}>
              <span>{triggerLine(t)}</span>
              {t.sources.map((s, j) =>
                s.url ? (
                  <ExternalLink key={j} href={s.url}>
                    {s.source} {s.ref}
                  </ExternalLink>
                ) : (
                  <span key={j} className="hb-src">
                    {s.source} {s.ref}
                  </span>
                ),
              )}
              {t.sources.length === 0 && <span className="hb-src">no source stated</span>}
            </span>
          ))}
        </span>
      </span>
    </td>
  );
}

/** Four-bar neutral equalizer for the four heat percentiles (fresh, loud, leading, unextended); zero text nodes. */
function HeatEq({ heat }: { heat: Heat }) {
  return (
    <span className="hb-eq" aria-hidden="true">
      {HEAT_PARTS.map((p) => {
        const v = heat[p];
        const pct = v == null ? 0 : Math.max(12, Math.round(v * 100));
        return (
          <span key={p} className="hb-eq-slot">
            <span className={`hb-eq-bar${v == null ? " blank" : ""}`} style={{ height: `${pct}%` }} />
          </span>
        );
      })}
    </span>
  );
}

type RowProps = {
  r: HotRowOut;
  heat: Heat | undefined;
  /** a past view (the payload's clock is "public"): the countdown cells carry their dates too */
  past: boolean;
  /** near the bottom of the scroll container: flip the keys popover upward so it never clips */
  flipUp: boolean;
  onSelectThesis: DashboardProps["onSelectThesis"];
};

const Row = memo(function Row({ r, heat, past, flipUp, onSelectThesis }: RowProps) {
  const tape = tapeCells(r);
  const since = sinceArmCells(r);
  const mark = leadingMark(r);
  const n = r.also_armed.length;
  const score = heat?.score ?? null;
  const kind = KIND_TAG[r.kind];
  return (
    <tr data-testid="hb-row" className={`hb-row ${r.kind}`}>
      <td className="hb-tk">
        <button
          type="button"
          className="hb-tkbtn"
          title={`Open ${r.thesis_name} on ${r.ticker}${r.name ? ` (${r.name})` : ""}`}
          onClick={() => onSelectThesis(r.thesis_id, r.ticker)}
        >
          {r.ticker}
        </button>
      </td>
      <td className="hb-card-td">
        <button type="button" className="hb-card" title={`Open ${r.thesis_name}`} onClick={() => onSelectThesis(r.thesis_id)}>
          {r.thesis_name}
        </button>
        {n > 0 && (
          <span className="hb-also" title={`Also armed on: ${r.also_armed.map((c) => c.thesis_name).join(", ")}`}>
            {` also armed on ${plural(n, "other card", "other cards")}`}
          </span>
        )}
      </td>
      <td className="hb-num">
        <span className="hb-heat" title={heatTitle(heat)}>
          {score !== null && <span className="hb-heat-bar" style={{ width: `${Math.max(0, Math.min(100, score))}%` }} />}
          {score !== null && heat && <HeatEq heat={heat} />}
          <span className={`hb-heat-n${score === null ? " gray" : ""}`}>{heatText(heat)}</span>
        </span>
      </td>
      <td className="hb-gstart">
        {kind && <span className={`hb-kind ${r.kind}`}>{kind}</span>}
        <span className="hb-cell" title={r.kind === "armed" ? "The member's verdict on the recorded call" : undefined}>
          {verdictWords(r)}
        </span>
      </td>
      <td>
        <span className="hb-cell">{r.entry_grade ?? "—"}</span>
      </td>
      <td className="hb-num">
        <span className="hb-cell" title="Setup strength: an experimental relative indicator, not a probability">
          {strengthWords(r)}
        </span>
      </td>
      <KeysCell r={r} flipUp={flipUp} />
      <CellTd cell={riskCell(r)} />
      <CellTd cell={armNightCell(r)} gstart />
      <CellTd cell={sinceCell(r)} />
      <CellTd cell={leftCell(r)} />
      <CellTd cell={exitCell(r, past)} lapsing={r.lapsing} />
      <CellTd cell={since.ret} gstart />
      <CellTd cell={since.iwm} />
      <CellTd cell={tape.rvol} gstart />
      <CellTd cell={tape.vs50} />
      <CellTd cell={tape.offHigh} />
      <CellTd cell={tape.vsIwm} mark={mark ? { text: "RS", title: mark } : null} />
      <CellTd cell={tape.ret7} />
      <CellTd cell={tape.ret30} />
      <CellTd cell={insiderCell(r)} />
      <CellTd cell={tape.adv} gstart />
      <CellTd cell={capCell(r)} />
      <td>
        {r.tape.flag === null ? (
          <Spark values={r.tape.spark} sessionsSince={r.clocks.sessions_since} />
        ) : (
          <span className="hb-cell gray">—</span>
        )}
      </td>
    </tr>
  );
});

export default function HotBoard({ asof, onSelectThesis }: DashboardProps) {
  const [toggles, setToggles] = useState<Toggles>(DEFAULT_TOGGLES);
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  // the default read: the armed rows with their tape, and the other kinds as counts
  const q = useHotBoard(asof, "armed");
  // the full read waits for a checked toggle that adds rows; it is requested once, then cached
  const armedCounts = q.data ? toggleCounts(q.data.candidate_counts) : NO_COUNTS;
  const needAll = TOGGLE_KINDS.some((k) => toggles[k] && armedCounts[k] > 0);
  const full = useHotBoard(asof, "all", needAll);
  // the full read counts only for THIS as-of (a placeholder is the previous as-of's); until it lands, the armed
  // read stays on screen
  const fullData = needAll && !full.isPlaceholderData ? full.data : undefined;
  const data = fullData ?? q.data;
  const pendingRest = needAll && fullData === undefined;

  const counts = useMemo(() => (data ? toggleCounts(data.candidate_counts) : NO_COUNTS), [data]);
  const onShow = useMemo(() => rowsOnShow(data?.rows ?? [], toggles), [data, toggles]);
  const controls = useMemo(() => filterControls(onShow), [onShow]);
  const shown = useMemo(() => onShow.filter((r) => rowMatches(r, filters)), [onShow, filters]);
  // the heat is among the rows SHOWN: a toggle or a filter changes it
  const heats = useMemo(() => heatByKey(shown), [shown]);
  const sorted = useMemo(() => sortRows(shown, sort, heats), [shown, sort, heats]);
  const scored = useMemo(() => shown.filter((r) => heats.get(r.key)?.score != null).length, [shown, heats]);

  const onSort = useCallback((key: SortKey) => setSort((s) => nextSort(s, key)), []);
  const flip = <T,>(list: readonly T[], v: T): T[] => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  if (q.isLoading || (!data && q.isFetching)) return <div className="dash-note">{SLOW}</div>;
  if (q.error && !data) return <div className="dash-note">Could not load the Hot Board.</div>;
  if (!data) return <div className="dash-note">{SLOW}</div>;

  const filtering = filtersActive(filters);
  const fetching = q.isFetching || (needAll && full.isFetching);
  const stale = fetching && data.asof !== asof;
  const restFailed = pendingRest && !full.isFetching && full.error != null;
  const lines = workLines(data, shown, counts, toggles, scored);
  const toggleOn = TOGGLE_KINDS.filter((k) => counts[k] > 0);

  return (
    <div className="hb">
      <RecordStartNote empty={data.rows.length === 0} />
      <div className="hb-controls">
        {toggleOn.length > 0 && (
          <span className="hb-field" role="group" aria-label="Add rows">
            {toggleOn.map((k) => (
              <label key={k} className="hb-check" title={data.rows_loaded === "all" ? TOGGLE_TITLE[k] : `${TOGGLE_TITLE[k]}. Loads the rows the first time one of these is checked.`}>
                <input
                  type="checkbox"
                  checked={toggles[k]}
                  onChange={(e) => setToggles((t) => ({ ...t, [k]: e.target.checked }))}
                />
                {TOGGLE_LABEL[k]} <span className="hb-n">+{counts[k].toLocaleString("en-US")}</span>
              </label>
            ))}
          </span>
        )}
        {controls.grades.length > 0 && (
          <span className="hb-field" role="group" aria-label="Grade">
            <span className="hb-seg-label">Grade</span>
            {controls.grades.map((g: GradePick) => (
              <button
                key={g}
                type="button"
                className={`hb-chip${filters.grades.includes(g) ? " on" : ""}`}
                aria-pressed={filters.grades.includes(g)}
                onClick={() => setFilters((f) => ({ ...f, grades: flip(f.grades, g) }))}
              >
                {g}
              </button>
            ))}
          </span>
        )}
        {controls.triggerKinds.length > 0 && (
          <span className="hb-field" role="group" aria-label="Trigger kind">
            <span className="hb-seg-label">Trigger</span>
            {controls.triggerKinds.map((k) => (
              <button
                key={k}
                type="button"
                className={`hb-chip${filters.triggerKinds.includes(k) ? " on" : ""}`}
                aria-pressed={filters.triggerKinds.includes(k)}
                onClick={() => setFilters((f) => ({ ...f, triggerKinds: flip(f.triggerKinds, k) }))}
              >
                {kindWord(k)}
              </button>
            ))}
          </span>
        )}
        {controls.cards.length > 0 && (
          <label className="hb-field">
            <span className="hb-seg-label">Card</span>
            <select
              className="hb-select"
              aria-label="Card"
              value={filters.card ?? ""}
              onChange={(e) => setFilters((f) => ({ ...f, card: e.target.value || null }))}
            >
              <option value="">all cards</option>
              {controls.cards.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {controls.tiers.length > 0 && (
          <span className="hb-field" role="group" aria-label="Cap tier">
            <span className="hb-seg-label">Cap tier</span>
            {controls.tiers.map((t: TierPick) => (
              <button
                key={t}
                type="button"
                className={`hb-chip${filters.tiers.includes(t) ? " on" : ""}`}
                aria-pressed={filters.tiers.includes(t)}
                title={t === "none" ? "Rows with no approximate cap" : `Rows in the ${t} band (approximate cap)`}
                onClick={() => setFilters((f) => ({ ...f, tiers: flip(f.tiers, t) }))}
              >
                {tierLabel(t)}
              </button>
            ))}
          </span>
        )}
        {controls.openOnly && (
          <label className="hb-check" title="Only rows whose entry window (to arm_until) still has a session left after the as-of">
            <input
              type="checkbox"
              checked={filters.openOnly}
              onChange={(e) => setFilters((f) => ({ ...f, openOnly: e.target.checked }))}
            />
            Entry window open only
          </label>
        )}
        {onShow.length > 1 && (
          <input
            type="search"
            className="hb-search"
            aria-label="Search tickers or cards"
            placeholder="Search ticker or card"
            value={filters.search ?? ""}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          />
        )}
        {filtering && (
          <button type="button" className="hb-clear" onClick={() => setFilters(DEFAULT_FILTERS)}>
            clear filters
          </button>
        )}
        {(sort.key !== DEFAULT_SORT.key || sort.dir !== DEFAULT_SORT.dir) && (
          <button type="button" className="hb-clear" onClick={() => setSort(DEFAULT_SORT)}>
            back to heat
          </button>
        )}
        {(fetching || stale) && (
          <span className="dash-note">
            {stale ? SLOW : pendingRest && !q.isFetching ? "reading the warming, watch and managing rows…" : "updating…"}
          </span>
        )}
        {restFailed && (
          <span className="dash-note">
            Could not load the other rows; the armed names stay on screen. Uncheck the toggles and check one again to retry.
          </span>
        )}
      </div>

      <div className="dash-note hb-work" data-testid="hb-count">
        {plural(shown.length, "row", "rows")}
        {shown.length !== onShow.length ? ` of ${onShow.length.toLocaleString("en-US")}` : ""}
        {filtering ? " after the filters" : ""} · heat is among the rows shown
      </div>

      {data.rows.length === 0 ? (
        <div className="dash-note hb-empty">
          No counted card&apos;s recorded call in effect at this as-of shows an armed name ({plural(data.cards_counted, "card", "cards")}{" "}
          counted, {data.cards_no_call} with no call yet).
        </div>
      ) : shown.length === 0 ? (
        <div className="dash-note hb-empty">No row passes the filters.</div>
      ) : (
        <div className="hb-scroll">
          <table className="hb-table">
            <thead>
              <tr className="hb-grp-row" aria-hidden="true">
                <td className="hb-grp hb-grp-tk" />
                <td className="hb-grp" colSpan={2}>Identity &amp; heat</td>
                <td className="hb-grp hb-gstart" colSpan={5}>Call</td>
                <td className="hb-grp hb-gstart" colSpan={4}>Clocks</td>
                <td className="hb-grp hb-gstart" colSpan={2}>Since arm</td>
                <td className="hb-grp hb-gstart" colSpan={7}>Tape</td>
                <td className="hb-grp hb-gstart" colSpan={3}>Size &amp; 90d</td>
              </tr>
              <tr>
                <th className="hb-tk" aria-sort={sort.key === "ticker" ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
                  <button type="button" className={`hb-sort${sort.key === "ticker" ? " on" : ""}`} title="Sort by ticker, A to Z" onClick={() => onSort("ticker")}>
                    Ticker
                    {sort.key === "ticker" && (
                      <span className="hb-arrow" aria-hidden="true">
                        {sort.dir === "asc" ? " ↑" : " ↓"}
                      </span>
                    )}
                  </button>
                </th>
                {COLUMNS.map((c) => (
                  <SortTh key={c.label} col={c} sort={sort} onSort={onSort} />
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((r, i) => (
                <Row
                  key={r.key}
                  r={r}
                  heat={heats.get(r.key)}
                  past={data.clock === "public"}
                  flipUp={sorted.length > 3 && i >= sorted.length - 3}
                  onSelectThesis={onSelectThesis}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="dash-note hb-formula" data-testid="hb-formula">
        {FORMULA}
      </div>
      {lines.map((line, i) => (
        <div key={i} className="dash-note hb-work">
          {line}
        </div>
      ))}
      <FlaggedNote flagged={data.flagged} title={flaggedRule("their price cells read \"flagged\" and they have no heat. They keep their row and their insider counts.")} />
      <ul className="dash-note hb-limits">
        {LIMITS.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  );
}

const TOGGLE_LABEL: Record<(typeof TOGGLE_KINDS)[number], string> = {
  warming: "Warming names",
  watch: "Watch names",
  managing: "Managing",
};
const TOGGLE_TITLE: Record<(typeof TOGGLE_KINDS)[number], string> = {
  warming: "Names with a live entry trigger on a Warming card (one key turned)",
  watch: "An Armed card's confirmation-only members (moving, no conviction yet)",
  managing: "A Managing card's held and armed members",
};
