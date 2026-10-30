import { memo, useMemo, useState } from "react";

import { FlaggedNote } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./track-record.css";
import {
  DEFAULT_FILTERS,
  HORIZONS,
  LIMITS,
  MIN_N,
  UNPINNED,
  capWords,
  cellMembers,
  cellView,
  chipGroups,
  filtersActive,
  liquidityWords,
  measureWords,
  plural,
  rowMatches,
  sliceKey,
  spanOf,
  spanView,
  tableRows,
  workLines,
  type ChipOption,
  type Comparison,
  type EntryRule,
  type Filters,
  type Horizon,
  type SliceBy,
  type TableRowView,
} from "./recordView";
import { useTrackRecord, type TrackRowOut } from "./useTrackRecord";

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
      <span className="tr-date">{r.arm_date}</span>
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

const LEFT_OUT: { reason: NonNullable<TrackRowOut["left_out"]>; words: string; hint: string }[] = [
  { reason: "flagged", words: "flagged price history", hint: "Their data flags reach their price readings, so they are in no cell." },
  { reason: "censored_start", words: "censored start", hint: "Armed on the card's first recorded night: the true arm date is unknown." },
  { reason: "ingest_flagged", words: "ingest flagged", hint: "The arm rested on a flagged ingest run; the Scoreboard leaves it out of its metrics too." },
];

type EditionPick = "record" | "newest";

export default function TrackRecord({ asof, onSelectThesis }: DashboardProps) {
  // The Scoreboard edition read: 0 = the record (the default while editions are paused), N = a stored re-run. Each
  // is its own server read, so it is a request parameter; everything else on the page is picked client side.
  const [edition, setEdition] = useState(0);
  const q = useTrackRecord(asof, edition);
  const data = q.data;
  const [slice, setSlice] = useState<SliceBy>("tier");
  const [comparison, setComparison] = useState<Comparison>("iwm");
  const [entry, setEntry] = useState<EntryRule>("next_session");
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [showThin, setShowThin] = useState(false);
  const [picked, setPicked] = useState<{ row: string; horizon: Horizon } | null>(null);
  const [limit, setLimit] = useState(LIST_STEP);

  const counted = useMemo(() => (data?.rows ?? []).filter((r) => r.left_out == null), [data]);
  const matching = useMemo(() => counted.filter((r) => rowMatches(r, filters)), [counted, filters]);
  const minN = data?.min_n ?? MIN_N;
  const table = useMemo(() => tableRows(matching, slice, entry, comparison, minN), [matching, slice, entry, comparison, minN]);
  const chips = useMemo(() => chipGroups(counted), [counted]);

  const pickedRow: TableRowView | undefined = picked ? table.find((r) => r.key === picked.row) : undefined;
  const shown: TrackRowOut[] = useMemo(() => {
    if (pickedRow && picked) return cellMembers(pickedRow.members, entry, picked.horizon, comparison);
    return matching.filter((r) => sliceKey(r, slice) !== UNPINNED);
  }, [pickedRow, picked, matching, slice, entry, comparison]);
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
      <div className="tr-controls">
        {newest && (
          <Seg label="Edition" value={edition === 0 ? "record" : "newest"} options={editionOptions} onPick={pickEdition} />
        )}
        <Seg label="Slice" value={slice} options={SLICES} onPick={(v) => reset({ slice: v })} />
        <Seg label="Compare" value={comparison} options={COMPARISONS} onPick={(v) => reset({ comparison: v })} />
        <Seg label="Entry" value={entry} options={ENTRIES} onPick={(v) => reset({ entry: v })} />
        <label className="tr-check" title="Draw the cells under 10 episodes with their numbers, grayed">
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

      <div className="tr-work">
        {workLines(data, entry, comparison).map((line, i) => (
          <div key={i} className="dash-note tr-line">
            {line}
          </div>
        ))}
        {filtering && (
          <div className="dash-note tr-line">
            filters on: {plural(matching.length, "counted episode")} of {counted.length.toLocaleString("en-US")} shown
          </div>
        )}
      </div>

      {data.counts.episodes === 0 ? (
        <div className="dash-note tr-empty">No arm episode is in the record at this as-of.</div>
      ) : (
        <>
          <div className="tr-scroll">
            <table className="tr-table">
              <caption className="tr-caption">
                Each cell: median {measureWords(comparison)} · episodes above zero / episodes (n). Rows are {pool}, then
                unpinned, then the pooled row.
              </caption>
              <thead>
                <tr>
                  <th className="tr-key">{slice === "tier" ? "Cap tier at the event" : "Liquidity at the event"}</th>
                  <th className="tr-num">Episodes</th>
                  {HORIZONS.map((h) => (
                    <th key={h.key} className="tr-num">
                      {h.label}
                    </th>
                  ))}
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
                      const v = cellView(r.cells[h.key], h, comparison, entry, showThin);
                      const on = picked?.row === r.key && picked.horizon === h.key;
                      const inner = (
                        <>
                          <span className={`tr-med${v.tone ? ` ${v.tone}` : ""}${v.gray ? " gray" : ""}`}>{v.text}</span>
                          {v.sub && <span className="tr-sub">{v.sub}</span>}
                        </>
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

      <FlaggedNote flagged={data.flagged} />
      <ul className="dash-note tr-limits">
        {LIMITS.map((l) => (
          <li key={l}>{l}</li>
        ))}
      </ul>
    </div>
  );
}
