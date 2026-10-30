import { memo, useCallback, useMemo, useState } from "react";

import { FlaggedNote } from "../FlaggedNote";
import type { DashboardProps } from "../registry";
import "./theme-leaderboard.css";
import {
  CAP_TIERS,
  DEFAULT_FILTERS,
  DEFAULT_SORT,
  FORMULA,
  FRAMING,
  LIMITS,
  STATE_LABEL,
  above200Cell,
  adoptionCell,
  armedCell,
  breadthCell,
  capCell,
  cardMatches,
  filterControls,
  filtersActive,
  insidersCell,
  keysCell,
  namesCell,
  nextSort,
  plural,
  ret7Cell,
  rsCell,
  scoreText,
  scoreTitle,
  sortCards,
  stateOf,
  tapeCell,
  workLines,
  type Cell,
  type Ctx,
  type Filters,
  type Sort,
  type SortKey,
  type StateKey,
} from "./boardView";
import { scoreByKey, type Score } from "./score";
import { useThemeLeaderboard, type LeaderboardCardOut } from "./useThemeLeaderboard";

// Theme Leaderboard (docs/dashboards/theme-leaderboard.md): every counted card ranked by a stated composite of its
// tape, breadth, keys, adoption curve and insiders, the five parts beside the score. A tape, not a document: dense
// rows, tabular numerals in the mono font, a sticky header, no cards and no chrome. Display only. Every figure is
// computed server-side from the display members, the calls log and the term-mention table; this page filters the
// cards, recomputes the score among the cards it shows (score.ts), sorts and words them. Loudness marks the
// exception: the sign tone only on the signed tape figures, the Board's state color only on a state chip, no accent
// (this page has no exception to mark).

type Column = { key: SortKey; label: string; title: string; num?: boolean };

const COLUMNS: readonly Column[] = [
  { key: "card", label: "Card", title: "Sort by the card's name. Click a name to open the card" },
  { key: "state", label: "State", title: "The state of the card's recorded call in effect at the as-of (R: a night rebuilt by the backfill)" },
  { key: "score", label: "Score (stated formula)", title: "Sort by score: the mean of five percentiles among the cards shown", num: true },
  { key: "tape", label: "30D vs IWM", title: "Tape: the median 30-day return of the card's priced counting names minus IWM's, in percentage points", num: true },
  { key: "breadth", label: "Above 50d", title: "Breadth: the counting names above their 50-day average, x/n", num: true },
  { key: "above200", label: "Above 200d", title: "The counting names above their 200-day average, x/n (beside the breadth part, not one of the five)", num: true },
  { key: "keys", label: "Keys", title: "Keys: how many of the two keys the recorded call has turned (Managing counts as 2)", num: true },
  { key: "adoption", label: "Adoption", title: "Adoption: the slope of the documents mentioning the card's SIGNAL terms, the last three closed months against the three before", num: true },
  { key: "insiders", label: "Buyers 30d", title: "Insiders: distinct open-market buyers in the last 30 days, summed across the counting names", num: true },
  { key: "names", label: "Names", title: "Names priced / counting (Context and Bench never count); flagged price histories are left out of every price reading", num: true },
  { key: "ret7", label: "7D", title: "The median 7-day return of the priced counting names", num: true },
  { key: "rs", label: "RS leaders", title: "Counting names whose relative strength is at a 13-week high, of those with a reading", num: true },
  { key: "nano", label: "nano", title: "Counting names in the nano band (approximate cap under $50M)", num: true },
  { key: "micro", label: "micro", title: "Counting names in the micro band (approximate cap $50M to $300M)", num: true },
  { key: "small", label: "small", title: "Counting names in the small band (approximate cap $300M to $2B)", num: true },
  { key: "mid", label: "mid", title: "Counting names in the mid band (approximate cap $2B to $10B)", num: true },
  { key: "large", label: "large", title: "Counting names in the large band (approximate cap $10B or more)", num: true },
  { key: "armed", label: "Armed", title: "The armed names on the recorded call, for an Armed card", num: true },
];

const SLOW =
  "Reading every counted card's names and recorded call, one breadth read per card and the term-mention months: the first load after a run or a basket edit can take a moment.";

function SortTh({ col, sort, onSort }: { col: Column; sort: Sort; onSort: (key: SortKey) => void }) {
  const on = sort.key === col.key;
  return (
    <th className={col.num ? "tl-num" : undefined} aria-sort={on ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
      <button type="button" className={`tl-sort${on ? " on" : ""}`} title={col.title} onClick={() => onSort(col.key)}>
        {col.label}
        {on && (
          <span className="tl-arrow" aria-hidden="true">
            {sort.dir === "asc" ? " ↑" : " ↓"}
          </span>
        )}
      </button>
    </th>
  );
}

function CellTd({ cell, label, part }: { cell: Cell; label: string; part?: boolean }) {
  const cls = ["tl-cell", cell.tone ?? "", cell.gray ? "gray" : ""].filter(Boolean).join(" ");
  return (
    <td className={`tl-num${part ? " tl-part" : ""}`} data-label={label}>
      <span className={cls} title={cell.title}>
        {cell.text}
      </span>
      {cell.note && (
        <>
          {" "}
          <span className="tl-note" title={cell.title}>
            {cell.note}
          </span>
        </>
      )}
    </td>
  );
}

type RowProps = {
  c: LeaderboardCardOut;
  score: Score | undefined;
  ctx: Ctx;
  onSelectThesis: DashboardProps["onSelectThesis"];
};

const Row = memo(function Row({ c, score, ctx, onSelectThesis }: RowProps) {
  const state = stateOf(c);
  const total = score?.score ?? null;
  return (
    <tr data-testid="tl-row" className="tl-row">
      <td className="tl-td-card" data-label="Card">
        <button type="button" className="tl-card" title={`Open ${c.thesis_name}`} onClick={() => onSelectThesis(c.thesis_id)}>
          {c.thesis_name}
        </button>
      </td>
      <td className="tl-td-state" data-label="State">
        <span
          className={`tl-state ${state}`}
          title={c.recorded_asof ? `Recorded call in effect: the night of ${c.recorded_asof}` : "No recorded call by the as-of"}
        >
          {STATE_LABEL[state]}
        </span>
        {c.reconstructed && (
          <span className="tl-mark" title="This night was rebuilt by the backfill: reported, never scored">
            R
          </span>
        )}
      </td>
      <td className="tl-num tl-td-score" data-label="Score">
        <span className="tl-score" title={scoreTitle(score)}>
          {total !== null && <span className="tl-score-bar" style={{ width: `${Math.max(0, Math.min(100, total))}%` }} />}
          <span className={`tl-score-n${total === null ? " gray" : ""}`}>{scoreText(score)}</span>
        </span>
      </td>
      <CellTd label="30D vs IWM" cell={tapeCell(c, ctx)} part />
      <CellTd label="Above 50d" cell={breadthCell(c)} part />
      <CellTd label="Above 200d" cell={above200Cell(c)} />
      <CellTd label="Keys" cell={keysCell(c)} part />
      <CellTd label="Adoption" cell={adoptionCell(c, ctx)} part />
      <CellTd label="Buyers 30d" cell={insidersCell(c)} part />
      <CellTd label="Names" cell={namesCell(c)} />
      <CellTd label="7D" cell={ret7Cell(c, ctx)} />
      <CellTd label="RS leaders" cell={rsCell(c)} />
      {CAP_TIERS.map((tier) => (
        <CellTd key={tier} label={tier} cell={capCell(c, tier)} />
      ))}
      <CellTd label="Armed" cell={armedCell(c)} />
    </tr>
  );
});

export default function ThemeLeaderboard({ asof, onSelectThesis }: DashboardProps) {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [sort, setSort] = useState<Sort>(DEFAULT_SORT);
  const q = useThemeLeaderboard(asof);
  const data = q.data;

  const controls = useMemo(() => filterControls(data?.cards ?? []), [data]);
  const shown = useMemo(() => (data?.cards ?? []).filter((c) => cardMatches(c, filters)), [data, filters]);
  // the score is among the cards SHOWN: a filter changes it
  const scores = useMemo(() => scoreByKey(shown), [shown]);
  const sorted = useMemo(() => sortCards(shown, sort, scores), [shown, sort, scores]);

  const onSort = useCallback((key: SortKey) => setSort((s) => nextSort(s, key)), []);
  const flip = <T,>(list: readonly T[], v: T): T[] => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  if (q.isLoading || (!data && q.isFetching)) return <div className="dash-note">{SLOW}</div>;
  if (q.error && !data) return <div className="dash-note">Could not load the Theme Leaderboard.</div>;
  if (!data) return <div className="dash-note">{SLOW}</div>;

  const filtering = filtersActive(filters);
  const stale = q.isFetching && data.asof !== asof;
  const lines = workLines(data, shown, scores);

  return (
    <div className="tl">
      <div className="dash-note tl-framing" data-testid="tl-framing">
        {FRAMING}
      </div>

      <div className="tl-controls">
        {controls.states.length > 0 && (
          <span className="tl-field" role="group" aria-label="State">
            <span className="tl-seg-label">State</span>
            {controls.states.map((s: StateKey) => (
              <button
                key={s}
                type="button"
                className={`tl-chip${filters.states.includes(s) ? " on" : ""}`}
                aria-pressed={filters.states.includes(s)}
                onClick={() => setFilters((f) => ({ ...f, states: flip(f.states, s) }))}
              >
                {STATE_LABEL[s]}
              </button>
            ))}
          </span>
        )}
        {controls.adoption && (
          <label className="tl-check" title="Only cards with a SIGNAL term whose six closed months of counts are stored, so the adoption part is counted">
            <input
              type="checkbox"
              checked={filters.adoptionOnly}
              onChange={(e) => setFilters((f) => ({ ...f, adoptionOnly: e.target.checked }))}
            />
            has adoption months
          </label>
        )}
        {controls.search && (
          <input
            type="search"
            className="tl-search"
            aria-label="Search cards"
            placeholder="Search cards"
            value={filters.search}
            onChange={(e) => setFilters((f) => ({ ...f, search: e.target.value }))}
          />
        )}
        {filtering && (
          <button type="button" className="tl-clear" onClick={() => setFilters(DEFAULT_FILTERS)}>
            clear filters
          </button>
        )}
        {(sort.key !== DEFAULT_SORT.key || sort.dir !== DEFAULT_SORT.dir) && (
          <button type="button" className="tl-clear" onClick={() => setSort(DEFAULT_SORT)}>
            back to score
          </button>
        )}
        {(q.isFetching || stale) && <span className="dash-note">{stale ? SLOW : "updating…"}</span>}
      </div>

      <div className="dash-note tl-work" data-testid="tl-count">
        {plural(shown.length, "card", "cards")}
        {shown.length !== data.cards.length ? ` of ${data.cards.length.toLocaleString("en-US")}` : ""}
        {filtering ? " after the filters" : ""} · the score is among the cards shown
      </div>

      {data.cards.length === 0 ? (
        <div className="dash-note tl-empty">
          No card is counted at this as-of ({data.live_cards} live, {data.cards_not_yet_created} created after it).
        </div>
      ) : shown.length === 0 ? (
        <div className="dash-note tl-empty">No card passes the filters.</div>
      ) : (
        <div className="tl-scroll">
          <table className="tl-table">
            <thead>
              <tr>
                {COLUMNS.map((c) => (
                  <SortTh key={c.key} col={c} sort={sort} onSort={onSort} />
                ))}
              </tr>
            </thead>
            <tbody>
              {sorted.map((c) => (
                <Row key={c.thesis_id} c={c} score={scores.get(c.thesis_id)} ctx={data} onSelectThesis={onSelectThesis} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="dash-note tl-formula" data-testid="tl-formula">
        {FORMULA}
      </div>
      {lines.map((line, i) => (
        <div key={i} className="dash-note tl-work">
          {line}
        </div>
      ))}
      <FlaggedNote flagged={data.flagged} />
      <ul className="dash-note tl-limits">
        {LIMITS.map((l) => (
          <li key={l}>{l}</li>
        ))}
        <li>
          Cap mix counts the counting names by approximate cap band at the as-of (nano under $50M, micro $50M to $300M,
          small $300M to $2B, mid $2B to $10B, large $10B or more): a display slice, never an input.
        </li>
      </ul>
    </div>
  );
}
