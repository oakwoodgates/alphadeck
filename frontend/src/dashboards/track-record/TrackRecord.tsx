import { memo, useMemo, useState } from "react";

import { FlaggedNote } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./track-record.css";
import {
  DEFAULT_FILTERS,
  EPISODE_SORTS,
  HORIZONS,
  LIMITS,
  MIN_N,
  UNPINNED,
  capWords,
  cellDistribution,
  cellMembers,
  cellView,
  chipGroups,
  episodeMatchesQuery,
  filtersActive,
  fmtRate,
  fmtRet,
  iqrHalfRange,
  liquidityWords,
  measureWords,
  plural,
  rowMatches,
  sliceKey,
  sortEpisodes,
  spanOf,
  spanView,
  tableRows,
  toneOf,
  workLines,
  type Cell,
  type ChipOption,
  type Comparison,
  type EntryRule,
  type EpisodeSort,
  type Filters,
  type Horizon,
  type SliceBy,
  type TableRowView,
} from "./recordView";
import { useTrackRecord, type TrackRowOut } from "./useTrackRecord";
import { RecordStartNote } from "../RecordStartNote";

// Track Record by Cap Tier (docs/dashboards/track-record.md): what the tape did after each arm episode the Scoreboard
// records, by the name's market-cap tier AT THE EVENT, at 5 / 10 / 20 / 60 sessions and to the episode's own exit_by,
// against IWM and SPY, with n and how often it held in every cell. The server sends every episode's row (both entry
// rules, all five horizons, the cap and the liquidity at the event); the slice, the filters, the entry toggle and the
// comparison are picked here, with no request. Display only. A cell under 10 episodes prints its n and "too small to
// read"; the pooled row is last and says it is pooled; nothing here calls a result good or bad (the sign tone only
// marks the sign of a number): loudness marks the exception only (#7 / WB#3).

const LIST_STEP = 150;

const SLICES: { value: SliceBy; label: string; title: string }[] = [
  { value: "tier", label: "Cap tier", title: "Rows are the fixed market-cap bands, measured at the event" },
  { value: "liquidity", label: "Liquidity", title: "Rows are the 20-day average dollar volume bands before the event" },
];
const COMPARISONS: { value: Comparison; label: string; title: string }[] = [
  { value: "iwm", label: "vs IWM", title: "The name's return minus IWM's over the same two dates" },
  { value: "spy", label: "vs SPY", title: "The name's return minus SPY's over the same two dates" },
  { value: "raw", label: "raw", title: "The name's own return, with no comparison" },
];
const ENTRIES: { value: EntryRule; label: string; title: string }[] = [
  { value: "next_session", label: "Next session", title: "Entry at the first close after the arm date: the first one a trader could act on (the default)" },
  { value: "arm_night", label: "Arm night", title: "Entry at the first close on or after the arm date: the Scoreboard's own, which reconciles its forward return" },
];

function Seg<T extends string>({
  label,
  value,
  options,
  onPick,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string; title: string }[];
  onPick: (v: T) => void;
}) {
  return (
    <span className="tr-field" role="group" aria-label={label}>
      <span className="tr-seg-label">{label}</span>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`tr-chip${value === o.value ? " on" : ""}`}
          aria-pressed={value === o.value}
          title={o.title}
          onClick={() => onPick(o.value)}
        >
          {o.label}
        </button>
      ))}
    </span>
  );
}

function Chips({
  label,
  picked,
  options,
  onToggle,
}: {
  label: string;
  picked: readonly string[];
  options: readonly ChipOption[];
  onToggle: (value: string) => void;
}) {
  if (options.length < 2) return null; // a control that cannot discriminate does not render (WB#3)
  return (
    <span className="tr-field" role="group" aria-label={label}>
      <span className="tr-seg-label">{label}</span>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`tr-chip${picked.includes(o.value) ? " on" : ""}`}
          aria-pressed={picked.includes(o.value)}
          onClick={() => onToggle(o.value)}
        >
          {o.label} <span className="tr-chip-n">{o.n.toLocaleString("en-US")}</span>
        </button>
      ))}
    </span>
  );
}

const toggled = (xs: readonly string[], v: string) => (xs.includes(v) ? xs.filter((x) => x !== v) : [...xs, v]);

const KEY1_SHORT: Record<string, string> = {
  insider: "insider",
  activist: "activist",
  ratified_catalyst: "ratified catalyst",
  corporate_catalyst: "corporate catalyst",
  revenue_accel: "revenue accel.",
  theme: "theme",
};

const EpisodeRow = memo(function EpisodeRow({
  r,
  entry,
  comparison,
  onSelectThesis,
}: {
  r: TrackRowOut;
  entry: EntryRule;
  comparison: Comparison;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const cap = capWords(r);
  const liq = liquidityWords(r);
  return (
    <li className="tr-li" data-testid="tr-row">
      <button type="button" className="tr-tk" title="Open the Cockpit on this name" onClick={() => onSelectThesis(r.thesis_id, r.ticker)}>
        {r.ticker}
      </button>
      <button type="button" className="tr-card" title="Open the card" onClick={() => onSelectThesis(r.thesis_id)}>
        {r.thesis_name}
      </button>
      <span className="tr-date">
        {r.arm_date}
        {r.feed_stopped && (
          <span className="tr-ended" title={FEED_ENDED_HINT}>
            {" "}
            feed ended
          </span>
        )}
      </span>
      <span className={`tr-cap${cap.text === UNPINNED ? " gray" : ""}`} title={cap.title}>
        {cap.text}
      </span>
      <span className={`tr-liq${liq.text === UNPINNED ? " gray" : ""}`} title={liq.title}>
        {liq.text}
      </span>
      <span className="tr-k1">{r.key1_source ? (KEY1_SHORT[r.key1_source] ?? r.key1_source) : ""}</span>
      <span className="tr-grade">{r.entry_grade ?? ""}</span>
      <span className="tr-hzs">
        {HORIZONS.map((h) => {
          const v = spanView(spanOf(r, entry, h.key), comparison);
          return (
            <span
              key={h.key}
              className={`tr-hz${v.tone ? ` ${v.tone}` : ""}${v.gray ? " gray" : ""}`}
              data-h={h.label}
              title={v.title}
            >
              {v.text}
            </span>
          );
        })}
      </span>
    </li>
  );
});

function OutRow({ r, onSelectThesis }: { r: TrackRowOut; onSelectThesis: DashboardProps["onSelectThesis"] }) {
  return (
    <li className="tr-li out" data-testid="tr-out-row">
      <button type="button" className="tr-tk" onClick={() => onSelectThesis(r.thesis_id, r.ticker)}>
        {r.ticker}
      </button>
      <button type="button" className="tr-card" onClick={() => onSelectThesis(r.thesis_id)}>
        {r.thesis_name}
      </button>
      <span className="tr-date">{r.arm_date}</span>
      <span className="tr-note">{r.left_out_note ?? ""}</span>
    </li>
  );
}

function ListHead() {
  return (
    <li className="tr-li head" aria-hidden="true">
      <span>Ticker</span>
      <span>Card</span>
      <span>Armed</span>
      <span title="Approximate market cap at the event">Cap at event</span>
      <span title="20-day average dollar volume before the event">Liquidity</span>
      <span>Key 1</span>
      <span>Grade</span>
      <span className="tr-hzs">
        {HORIZONS.map((h) => (
          <span key={h.key} className="tr-hz">
            {h.key === "exit_by" ? "exit_by" : h.key.slice(1)}
          </span>
        ))}
      </span>
    </li>
  );
}

const FEED_ENDED_HINT =
  "Flag: price feed stopped. The company itself ended (delisted), so this episode is counted up to its last bar and the horizons past it read path ended.";

const LEFT_OUT: { reason: NonNullable<TrackRowOut["left_out"]>; words: string; hint: string }[] = [
  { reason: "flagged", words: "flagged price history", hint: "Their data flags reach their price readings, so they are in no cell." },
  { reason: "feed_stopped", words: "price feed stopped", hint: "Their price feed stopped while they are still on a live card: a data fault, so they are in no cell." },
  { reason: "no_longer_priced", words: "no longer priced", hint: "Off every live card, so the nightly stopped fetching their prices. The company may still trade; the path did not end, so they are in no cell." },
  { reason: "censored_start", words: "censored start", hint: "Armed on the card's first recorded night: the true arm date is unknown." },
  { reason: "ingest_flagged", words: "ingest flagged", hint: "The arm rested on a flagged ingest run; the Scoreboard leaves it out of its metrics too." },
];

// --- visual primitives: per-cell IQR whisker, horizon maturity bar, clicked-cell distribution strip ---------------

const WHISKER_W = 68;
const WHISKER_H = 14;
const WHISKER_PAD = 4;

/** Per-cell horizontal IQR whisker + median dot on a shared zero-centered scale (no text/title inside, keeping
 *  cell textContent clean). Clamps extreme quartiles to the edges with a subtle end tick. */
function CellWhisker({
  cell,
  halfRange,
  dim,
}: {
  cell: Cell;
  halfRange: number;
  dim: boolean;
}) {
  if (cell.q1 == null || cell.q3 == null || cell.median == null) return null;
  const inner = WHISKER_W - 2 * WHISKER_PAD;
  const cx = WHISKER_W / 2;
  const xOf = (v: number) => {
    const clamped = Math.max(-halfRange, Math.min(halfRange, v));
    return WHISKER_PAD + ((clamped + halfRange) / (2 * halfRange)) * inner;
  };
  const x1 = xOf(cell.q1);
  const x3 = xOf(cell.q3);
  const xm = xOf(cell.median);
  const clipLo = cell.q1 < -halfRange;
  const clipHi = cell.q3 > halfRange;
  const tone = dim ? "" : toneOf(cell.median);
  return (
    <svg
      className={`tr-whisker${dim ? " dim" : ""}`}
      width={WHISKER_W}
      height={WHISKER_H}
      viewBox={`0 0 ${WHISKER_W} ${WHISKER_H}`}
      aria-hidden="true"
      data-testid="tr-whisker"
    >
      <line className="tr-w-track" x1={WHISKER_PAD} x2={WHISKER_W - WHISKER_PAD} y1={WHISKER_H / 2} y2={WHISKER_H / 2} />
      <line className="tr-w-zero" x1={cx} x2={cx} y1={2} y2={WHISKER_H - 2} />
      <rect className="tr-w-iqr" x={x1} y={4.5} width={Math.max(x3 - x1, 1.5)} height={5} rx={1.5} />
      {clipLo && <line className="tr-w-clip" x1={WHISKER_PAD} x2={WHISKER_PAD} y1={3} y2={WHISKER_H - 3} />}
      {clipHi && <line className="tr-w-clip" x1={WHISKER_W - WHISKER_PAD} x2={WHISKER_W - WHISKER_PAD} y1={3} y2={WHISKER_H - 3} />}
      <circle className={`tr-w-med${tone ? ` ${tone}` : ""}`} cx={xm} cy={WHISKER_H / 2} r={2.8} />
    </svg>
  );
}

/** Stacked maturity bar under a horizon header: counted / maturing / path ended (no text nodes, aria-hidden). */
function HorizonBar({
  counted,
  maturing,
  ended,
}: {
  counted: number;
  maturing: number;
  ended: number;
}) {
  const total = counted + maturing + ended;
  if (total === 0) return null;
  return (
    <span className="tr-hbar" aria-hidden="true" data-testid="tr-hbar">
      {counted > 0 && <span className="tr-hb-counted" style={{ flexGrow: counted }} />}
      {maturing > 0 && <span className="tr-hb-maturing" style={{ flexGrow: maturing }} />}
      {ended > 0 && <span className="tr-hb-ended" style={{ flexGrow: ended }} />}
    </span>
  );
}

const STRIP_W = 640;
const STRIP_H = 54;
const STRIP_L = 14;
const STRIP_R = 14;

/** Clicked-cell horizontal episode dot plot: every matured episode in the picked cell plotted against 0%, with
 *  the interquartile band [q1, q3] and median line marked. Hovering shows the episode; clicking opens its Cockpit. */
function CellStrip({
  row,
  horizon,
  horizonLabel,
  entry,
  comparison,
  onSelectThesis,
}: {
  row: TableRowView;
  horizon: Horizon;
  horizonLabel: string;
  entry: EntryRule;
  comparison: Comparison;
  onSelectThesis: DashboardProps["onSelectThesis"];
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const cell = row.cells[horizon];
  const dist = useMemo(
    () => cellDistribution(row.members, entry, horizon, comparison),
    [row.members, entry, horizon, comparison],
  );
  if (dist.points.length === 0) return null;

  const inner = STRIP_W - STRIP_L - STRIP_R;
  const xOf = (v: number) => STRIP_L + ((v - dist.lo) / (dist.hi - dist.lo)) * inner;
  const x0 = xOf(0);
  const yMid = 22;
  const focus = hovered ? dist.points.find((p) => p.key === hovered) ?? null : null;

  return (
    <div className="tr-strip" data-testid="tr-strip">
      <div className="tr-strip-head">
        <span className="tr-strip-title">
          {row.label} · {horizonLabel} · {measureWords(comparison)}
        </span>
        {cell.median != null && (
          <span className="tr-strip-stats">
            median {fmtRet(cell.median)} · IQR {fmtRet(cell.q1)} to {fmtRet(cell.q3)} · held {cell.held}/{cell.n} ({fmtRate(cell.hitRate)})
          </span>
        )}
        {focus && (
          <span className="tr-strip-focus">
            {focus.row.ticker} · {focus.row.thesis_name} · {focus.row.arm_date} · {fmtRet(focus.value)}
          </span>
        )}
      </div>
      <svg
        className="tr-strip-svg"
        viewBox={`0 0 ${STRIP_W} ${STRIP_H}`}
        role="img"
        aria-label={`${row.label} ${horizonLabel} distribution across ${dist.points.length} episodes`}
      >
        {cell.q1 != null && cell.q3 != null && (
          <rect
            className="tr-s-iqr"
            x={xOf(cell.q1)}
            y={8}
            width={Math.max(xOf(cell.q3) - xOf(cell.q1), 2)}
            height={28}
            rx={2}
          />
        )}
        <line className="tr-s-track" x1={STRIP_L} x2={STRIP_W - STRIP_R} y1={yMid} y2={yMid} />
        <line className="tr-s-zero" x1={x0} x2={x0} y1={6} y2={38} />
        {cell.median != null && (
          <line className="tr-s-med" x1={xOf(cell.median)} x2={xOf(cell.median)} y1={6} y2={38} />
        )}
        {dist.points.map((p, idx) => {
          const px = xOf(p.value);
          // slight vertical stagger when episodes cluster at nearly the same return
          const jitter = ((idx % 3) - 1) * 4.5;
          const t = toneOf(p.value);
          const on = hovered === p.key;
          return (
            <circle
              key={p.key}
              className={`tr-s-dot${t ? ` ${t}` : ""}${on ? " on" : ""}`}
              cx={px}
              cy={yMid + jitter}
              r={on ? 4.5 : 3.2}
              onMouseEnter={() => setHovered(p.key)}
              onMouseLeave={() => setHovered(null)}
              onClick={() => onSelectThesis(p.row.thesis_id, p.row.ticker)}
            />
          );
        })}
        <text className="tr-s-tick" x={STRIP_L} y={STRIP_H - 3} textAnchor="start">
          {fmtRet(dist.points[0].value)}
        </text>
        <text className="tr-s-tick" x={x0} y={STRIP_H - 3} textAnchor="middle">
          0%
        </text>
        <text className="tr-s-tick" x={STRIP_W - STRIP_R} y={STRIP_H - 3} textAnchor="end">
          {fmtRet(dist.points[dist.points.length - 1].value)}
        </text>
      </svg>
    </div>
  );
}

// "other" = the server's default is a published edition that is neither the record nor the newest (a pinned
// default): no button is pressed then, and the work line names the edition read.
type EditionPick = "record" | "newest" | "other";

export default function TrackRecord({ asof, onSelectThesis }: DashboardProps) {
  // The Scoreboard edition read: none named = the server's default (the Scoreboard's: the newest published edition,
  // the record when none is published), and the reply says which it served; 0 = the record, N = a stored re-run, once
  // the operator picks. Each is its own server read, so it is a request parameter; everything else on the page is
  // picked client side.
  const [edition, setEdition] = useState<number | undefined>(undefined);
  const q = useTrackRecord(asof, edition);
  const data = q.data;
  const [slice, setSlice] = useState<SliceBy>("tier");
  const [comparison, setComparison] = useState<Comparison>("iwm");
  const [entry, setEntry] = useState<EntryRule>("next_session");
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [showThin, setShowThin] = useState(false);
  const [picked, setPicked] = useState<{ row: string; horizon: Horizon } | null>(null);
  const [listQuery, setListQuery] = useState("");
  const [listSort, setListSort] = useState<EpisodeSort>("armed");
  const [limit, setLimit] = useState(LIST_STEP);

  const counted = useMemo(() => (data?.rows ?? []).filter((r) => r.left_out == null), [data]);
  const matching = useMemo(() => counted.filter((r) => rowMatches(r, filters)), [counted, filters]);
  const minN = data?.min_n ?? MIN_N;
  const table = useMemo(() => tableRows(matching, slice, entry, comparison, minN), [matching, slice, entry, comparison, minN]);
  const halfRange = useMemo(() => iqrHalfRange(table, showThin), [table, showThin]);
  const chips = useMemo(() => chipGroups(counted), [counted]);

  const pickedRow: TableRowView | undefined = picked ? table.find((r) => r.key === picked.row) : undefined;
  const shown: TrackRowOut[] = useMemo(() => {
    const base =
      pickedRow && picked
        ? cellMembers(pickedRow.members, entry, picked.horizon, comparison)
        : matching.filter((r) => sliceKey(r, slice) !== UNPINNED);
    const filtered = listQuery.trim() ? base.filter((r) => episodeMatchesQuery(r, listQuery)) : base;
    return sortEpisodes(filtered, listSort, entry, comparison);
  }, [pickedRow, picked, matching, slice, entry, comparison, listQuery, listSort]);
  const unpinned = useMemo(() => matching.filter((r) => sliceKey(r, slice) === UNPINNED), [matching, slice]);
  const setAside = useMemo(() => (data?.rows ?? []).filter((r) => r.left_out != null), [data]);

  if (q.isLoading || (!data && q.isFetching)) {
    return <div className="dash-note">Reading the Scoreboard's episodes and each name's tape — the first load after a run can take a minute.</div>;
  }
  if (q.error || !data) return <div className="dash-note">Could not load the Track Record.</div>;

  const pool = (slice === "tier" ? "tiers" : "liquidity bands");
  const filtering = filtersActive(filters);
  const pickedWords = picked && pickedRow ? HORIZONS.find((h) => h.key === picked.horizon)?.label : null;
  const reset = (next: Partial<{ slice: SliceBy; comparison: Comparison; entry: EntryRule }>) => {
    if (next.slice) setSlice(next.slice);
    if (next.comparison) setComparison(next.comparison);
    if (next.entry) setEntry(next.entry);
    setPicked(null);
    setLimit(LIST_STEP);
  };
  // A selector with one option cannot discriminate (WB#3): the edition control shows only once an edition is published.
  const newest = data.newest_edition ?? null;
  const served = data.edition.number; // what the server read, whether the page named it or the default did
  const pressed: EditionPick = served === 0 ? "record" : newest && served === newest.number ? "newest" : "other";
  const editionOptions: { value: EditionPick; label: string; title: string }[] = newest
    ? [
        { value: "record", label: "The record", title: "The calls log as recorded each night: the Scoreboard's Edition 0" },
        {
          value: "newest",
          label: `Edition ${newest.number} (newest published)`,
          title: `${newest.label}: a stored re-run of the call rules, read from its frozen pages`,
        },
      ]
    : [];
  const pickEdition = (v: EditionPick) => {
    setEdition(v === "record" || !newest ? 0 : newest.number);
    setPicked(null);
    setLimit(LIST_STEP);
  };

  return (
    <div className="tr">
      <RecordStartNote empty={data.counts.episodes === 0} />
      <div className="tr-controls">
        {newest && (
          <Seg label="Edition" value={pressed} options={editionOptions} onPick={pickEdition} />
        )}
        <Seg label="Slice" value={slice} options={SLICES} onPick={(v) => reset({ slice: v })} />
        <Seg label="Compare" value={comparison} options={COMPARISONS} onPick={(v) => reset({ comparison: v })} />
        <Seg label="Entry" value={entry} options={ENTRIES} onPick={(v) => reset({ entry: v })} />
        <label className="tr-check" title={`Draw the cells under ${minN} episodes with their numbers, grayed`}>
          <input type="checkbox" checked={showThin} onChange={(e) => setShowThin(e.target.checked)} />
          show thin cells
        </label>
      </div>
      <div className="tr-controls">
        <label className="tr-check" title="Only the name each Armed call put first, against every armed member">
          <input
            type="checkbox"
            checked={filters.headline}
            onChange={(e) => {
              setFilters((f) => ({ ...f, headline: e.target.checked }));
              setPicked(null);
            }}
          />
          headline names only
        </label>
        <Chips
          label="Key 1"
          picked={filters.key1}
          options={chips.key1}
          onToggle={(v) => {
            setFilters((f) => ({ ...f, key1: toggled(f.key1, v) }));
            setPicked(null);
          }}
        />
        <Chips
          label="Grade"
          picked={filters.grade}
          options={chips.grade}
          onToggle={(v) => {
            setFilters((f) => ({ ...f, grade: toggled(f.grade, v) }));
            setPicked(null);
          }}
        />
        <Chips
          label="Armed with"
          picked={filters.coArm}
          options={chips.coArm}
          onToggle={(v) => {
            setFilters((f) => ({ ...f, coArm: toggled(f.coArm, v) }));
            setPicked(null);
          }}
        />
        {filtering && (
          <button
            type="button"
            className="tr-clear"
            onClick={() => {
              setFilters(DEFAULT_FILTERS);
              setPicked(null);
            }}
          >
            clear filters
          </button>
        )}
        {q.isFetching && <span className="dash-note">updating…</span>}
      </div>

      {filtering && (
        <div className="dash-note tr-line">
          filters on: {plural(matching.length, "counted episode")} of {counted.length.toLocaleString("en-US")} shown
        </div>
      )}

      {data.counts.episodes === 0 ? (
        <div className="dash-note tr-empty">No arm episode is in the record at this as-of.</div>
      ) : (
        <>
          <div className="tr-scroll">
            <table className="tr-table">
              <caption className="tr-caption">
                Each cell: median {measureWords(comparison)} · episodes above zero / episodes (n) · interquartile range
                whisker on a shared ±{Math.round(halfRange * 100)}% zero-centered scale. Rows are {pool}, then
                unpinned, then the pooled row.
              </caption>
              <thead>
                <tr>
                  <th className="tr-key">{slice === "tier" ? "Cap tier at the event" : "Liquidity at the event"}</th>
                  <th className="tr-num">Episodes</th>
                  {HORIZONS.map((h) => {
                    const hc = data.counts[entry][h.key];
                    const tip = `${h.label}: ${hc.counted} counted, ${hc.maturing} maturing${hc.path_ended > 0 ? `, ${hc.path_ended} path ended` : ""}`;
                    return (
                      <th key={h.key} className="tr-num" title={tip}>
                        <span className="tr-th-wrap">
                          <span>{h.label}</span>
                          <HorizonBar counted={hc.counted} maturing={hc.maturing} ended={hc.path_ended} />
                        </span>
                      </th>
                    );
                  })}
                </tr>
              </thead>
              <tbody>
                {table.map((r) => (
                  <tr key={r.key} className={r.key === "pooled" ? "tr-pooled" : undefined} data-testid="tr-table-row" data-row={r.key}>
                    <th scope="row" className="tr-key">
                      <span className="tr-key-label">{r.label}</span>
                      {r.band && <span className="tr-band">{r.band}</span>}
                    </th>
                    <td className="tr-num">{r.episodes.toLocaleString("en-US")}</td>
                    {HORIZONS.map((h) => {
                      const cell = r.cells[h.key];
                      const v = cellView(cell, h, comparison, entry, showThin);
                      const on = picked?.row === r.key && picked.horizon === h.key;
                      const showWhisker = cell.n > 0 && (!cell.tooSmall || showThin);
                      const inner = (
                        <span className="tr-cell-body">
                          {showWhisker && (
                            <CellWhisker cell={cell} halfRange={halfRange} dim={cell.tooSmall || r.key === "pooled"} />
                          )}
                          <span className="tr-cell-nums">
                            <span className={`tr-med${v.tone ? ` ${v.tone}` : ""}${v.gray ? " gray" : ""}`}>{v.text}</span>
                            {v.sub && <span className="tr-sub">{v.sub}</span>}
                          </span>
                        </span>
                      );
                      return (
                        <td key={h.key} className="tr-num" data-testid={`tr-cell-${r.key}-${h.key}`}>
                          {v.clickable ? (
                            <button
                              type="button"
                              className={`tr-cell${on ? " on" : ""}`}
                              aria-pressed={on}
                              title={v.title}
                              onClick={() => {
                                setPicked(on ? null : { row: r.key, horizon: h.key });
                                setLimit(LIST_STEP);
                              }}
                            >
                              {inner}
                            </button>
                          ) : (
                            <span className="tr-cell static" title={v.title}>
                              {inner}
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {pickedRow && picked && pickedWords && (
            <CellStrip
              row={pickedRow}
              horizon={picked.horizon}
              horizonLabel={pickedWords}
              entry={entry}
              comparison={comparison}
              onSelectThesis={onSelectThesis}
            />
          )}

          <div className="tr-listbar">
            <div className="tr-listhead dash-note">
              {pickedRow && picked ? (
                <>
                  {plural(shown.length, "episode")} in {pickedRow.label} · {pickedWords} ·{" "}
                  <button type="button" className="tr-clear" onClick={() => setPicked(null)}>
                    show every episode
                  </button>
                </>
              ) : (
                <>
                  {plural(shown.length, "counted episode")}, newest arm first
                  {unpinned.length > 0 ? ` (${unpinned.length.toLocaleString("en-US")} more unpinned, below)` : ""}
                </>
              )}
            </div>
            <div className="tr-list-tools">
              <input
                type="search"
                className="tr-search"
                aria-label="Filter episodes by ticker or card"
                placeholder="Filter ticker or card…"
                value={listQuery}
                onChange={(e) => {
                  setListQuery(e.target.value);
                  setLimit(LIST_STEP);
                }}
              />
              <select
                className="tr-select"
                aria-label="Sort episodes"
                value={listSort}
                onChange={(e) => setListSort(e.target.value as EpisodeSort)}
              >
                {EPISODE_SORTS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {shown.length > 0 && (
            <ul className="tr-list">
              <ListHead />
              {shown.slice(0, limit).map((r) => (
                <EpisodeRow
                  key={`${r.thesis_id}:${r.security_id}:${r.arm_date}`}
                  r={r}
                  entry={entry}
                  comparison={comparison}
                  onSelectThesis={onSelectThesis}
                />
              ))}
            </ul>
          )}
          {shown.length > limit && (
            <button type="button" className="tr-clear" onClick={() => setLimit((n) => n + LIST_STEP)}>
              show {Math.min(LIST_STEP, shown.length - limit).toLocaleString("en-US")} more of {(shown.length - limit).toLocaleString("en-US")} left
            </button>
          )}

          {unpinned.length > 0 && !picked && (
            <details className="dash-note tr-fold" data-testid="tr-unpinned">
              <summary>
                {plural(unpinned.length, "counted episode")} with {slice === "tier" ? "no pinned cap" : "no pinned liquidity"} — in the
                unpinned row and the pooled row
              </summary>
              <ul className="tr-list">
                <ListHead />
                {unpinned.map((r) => (
                  <EpisodeRow
                    key={`${r.thesis_id}:${r.security_id}:${r.arm_date}`}
                    r={r}
                    entry={entry}
                    comparison={comparison}
                    onSelectThesis={onSelectThesis}
                  />
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      <div className="tr-work">
        {workLines(data, entry, comparison).map((line, i) => (
          <div key={i} className="dash-note tr-line">
            {line}
          </div>
        ))}
      </div>

      {LEFT_OUT.map(({ reason, words, hint }) => {
        const rows = setAside.filter((r) => r.left_out === reason);
        if (rows.length === 0) return null;
        return (
          <details key={reason} className="dash-note tr-fold" data-testid={`tr-out-${reason}`}>
            <summary title={hint}>
              {plural(rows.length, "episode")} left out: {words}
            </summary>
            <ul className="tr-list out">
              {rows.map((r) => (
                <OutRow key={`${r.thesis_id}:${r.security_id}:${r.arm_date}`} r={r} onSelectThesis={onSelectThesis} />
              ))}
            </ul>
          </details>
        );
      })}

      <FlaggedNote flagged={data.flagged.filter((f) => !f.feed_ended)} />
      {data.counts.counted_feed_stopped > 0 && (
        <p className="dash-note" data-testid="tr-ended-note" title={FEED_ENDED_HINT}>
          {plural(data.counts.counted_feed_stopped, "episode")} counted although the price feed stopped (the company ended:{" "}
          {data.flagged
            .filter((f) => f.feed_ended)
            .map((f) => f.ticker)
            .join(", ")}
          ). A stopped feed counts until it stops, then reads path ended; any other flag leaves the episode out.
        </p>
      )}
      <ul className="dash-note tr-limits">
        {LIMITS.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  );
}
